"""Opt-in material/geometry quality policy. No character or background IDs.

Operates only on in-memory selected assets; source .blend files are never saved.
This candidate is shared by the demo adapter and future production adapters.
"""
import re

VERSION = 'detail_candidate_v4.2'
DEFAULTS = dict(skin_normals=True, restore_body_subdivision=True,
                skin_material=True, hair_material=True, skin_roughness_floor=0.38,
                skin_coat_floor=0.28, hair_bump_gain=0.65)


def semantic(name):
    text = name.lower()
    if re.search(r'(^|[ _-])(skin|face|torso|arms?|legs?|ears?|lips)([ _.-]|$)', text):
        return 'skin'
    if any(token in text for token in ('hair', 'scalp', 'wisps', 'ponytail', 'braid')):
        return 'hair'
    return 'unknown'


def background_statistics(rgb_bytes):
    """Display-space scene contrast proxy, not recovered HDR illumination."""
    pixels=[tuple(v/255. for v in rgb_bytes[i:i+3]) for i in range(0,len(rgb_bytes),3)]
    if not pixels or any(len(p)!=3 for p in pixels):raise ValueError('Invalid RGB reference')
    ranked=sorted((0.2126*r+0.7152*g+0.0722*b,(r,g,b)) for r,g,b in pixels)
    n=len(ranked)
    p10,p50,p90=[ranked[min(n-1,int(q*(n-1)))][0] for q in (.1,.5,.9)]
    contrast=(p90-p10)/max(.05,p90+p10)
    mid=[p for _,p in ranked[int(n*.2):max(int(n*.2)+1,int(n*.65))]]
    color=[sum(p[c] for p in mid)/len(mid) for c in range(3)]
    top=max(max(color),.001)
    tint=[max(.65,c/top) for c in color]
    return dict(method='display_luminance_percentiles_v1',p10=p10,p50=p50,p90=p90,
                contrast=contrast,ambient_tint=tint,physical_calibration=False)


def adapt_lighting(scene, metrics):
    if not metrics:return dict(status='skipped',reason='missing_background_reference')
    if metrics.get('method')!='display_luminance_percentiles_v1':
        raise ValueError('Unsupported background lighting statistics')
    p90=float(metrics['p90']);p50=float(metrics['p50'])
    contrast=max(0.,min(1.,float(metrics['contrast'])))
    if p90 < .06:
        return dict(status='skipped',reason='background_too_dark_for_reliable_statistics')
    # Bounded response prevents per-background tuning and avoids dramatic
    # exposure swings. Sample once per clip, never adapt light per video frame.
    fill_gain=max(.45,min(1.,1.5*p50/max(.05,p90)))
    size_gain=max(.5,1.-.5*contrast)
    direction=str(scene.get('rm_scene_lighting_direction','unknown'))
    if direction in ('diffuse','unknown'):size_gain=max(.85,size_gain)
    changes=[]
    for obj in scene.objects:
        if obj.type!='LIGHT' or obj.hide_render:continue
        role=obj.name.split('.')[0]
        if role not in ('Key','Fill'):continue
        before=dict(energy=obj.data.energy,size=obj.data.size,color=list(obj.data.color))
        if role=='Fill':
            obj.data.energy*=fill_gain
            obj.data.color=tuple(.8*c+.2*t for c,t in zip(obj.data.color,metrics['ambient_tint']))
        else:obj.data.size*=size_gain
        changes.append(dict(role=role,before=before,after=dict(energy=obj.data.energy,
                       size=obj.data.size,color=list(obj.data.color)),position=list(obj.location)))
    return dict(status='applied_candidate',reference=metrics,fill_gain=fill_gain,
                key_size_gain=size_gain,changes=changes,
                limitations='heuristic contrast matching; no recovered HDR, perspective or contact shadow')


def active_nodes(material):
    """Follow the active Surface socket, including only used group outputs.

Return (tree, node) pairs. Group inputs are mapped to their actual instance.
This excludes unconnected nodes and unused group outputs. Mix branch weights
are not evaluated: both linked branches remain potentially active.
"""
    found, seen = {}, set()
    def match(sockets, socket):
        return next((s for s in sockets if s.identifier == socket.identifier),
                    sockets.get(socket.name))
    def visit_input(tree, socket, parents):
        if socket is None: return
        for link in socket.links:
            if link.is_valid:
                visit_output(tree, link.from_node, link.from_socket, parents)
    def visit_output(tree, node, socket, parents):
        key = (tree.as_pointer(), node.as_pointer(), socket.identifier,
               tuple(p[1].as_pointer() for p in parents))
        if key in seen: return
        seen.add(key)
        found[(tree.as_pointer(),node.as_pointer())] = (tree,node)
        if node.type == 'GROUP_INPUT':
            if parents:
                outer, instance = parents[-1]
                visit_input(outer,match(instance.inputs,socket),parents[:-1])
        elif node.type == 'GROUP' and node.node_tree:
            output = next((n for n in node.node_tree.nodes
                           if n.type=='GROUP_OUTPUT' and n.is_active_output), None)
            if output:
                visit_input(node.node_tree,match(output.inputs,socket),parents+[(tree,node)])
        elif node.type == 'REROUTE':
            visit_input(tree,node.inputs[0],parents)
        elif not node.mute:
            for s in node.inputs: visit_input(tree,s,parents)
        else:
            for link in node.internal_links:
                if link.to_socket == socket: visit_input(tree,link.from_socket,parents)
    if material.use_nodes:
        for n in material.node_tree.nodes:
            if n.type=='OUTPUT_MATERIAL' and n.is_active_output:
                visit_input(material.node_tree,n.inputs.get('Surface'),[])
    return list(found.values())


def apply_quality(meshes, config=None):
    import bpy
    opts = {**DEFAULTS, **(config or {})}
    unknown = set(opts)-set(DEFAULTS)
    if unknown: raise ValueError('Unknown quality options: '+str(sorted(unknown)))
    report = dict(version=VERSION, options=opts, changes=[], skipped=[], materials=[],
                  alpha_policy='preserve_source_coverage_no_new_opacity_operations',
                  source_files_saved=False)
    changes, skipped = report['changes'], report['skipped']
    # Classification is a conservative schema hint, not an identity lookup.
    materials = {s.material for o in meshes for s in o.material_slots
                 if s.material and s.material.use_nodes}
    roles = {m:semantic(m.name) for m in materials}
    for obj in meshes:
        if obj.type!='MESH': continue
        skin_slots = {i for i,s in enumerate(obj.material_slots)
                      if roles.get(s.material)=='skin'}
        skin_faces = [p for p in obj.data.polygons if p.material_index in skin_slots]
        skin_fraction = len(skin_faces)/max(1,len(obj.data.polygons))
        if skin_fraction < 0.5 or not any(m.type=='ARMATURE' for m in obj.modifiers):
            continue
        if opts['skin_normals'] and obj.data.has_custom_normals:
            # Keep non-skin custom normals (nails, eyes, hard accessories).
            obj.data = obj.data.copy()
            normals = [tuple(n.vector) for n in obj.data.corner_normals]
            count=0
            for p in obj.data.polygons:
                if p.material_index in skin_slots and p.use_smooth:
                    for i in p.loop_indices:
                        normals[i]=(0.,0.,0.)
                        count+=1
            obj.data.normals_split_custom_set(normals)
            changes.append(dict(object=obj.name,rule='recompute_smooth_skin_corner_normals',
                                loops=count,skin_face_fraction=skin_fraction,
                                topology_changed=False))
        if opts['restore_body_subdivision']:
            for modifier in obj.modifiers:
                if modifier.type=='SUBSURF' and not modifier.show_render:
                    if modifier.subdivision_type != 'CATMULL_CLARK':
                        skipped.append(dict(object=obj.name,reason='non_catmull_subdivision'));continue
                    before = dict(show_render=modifier.show_render,show_viewport=modifier.show_viewport,
                                  levels=modifier.levels,render_levels=modifier.render_levels)
                    modifier.show_render=True
                    modifier.show_viewport=True
                    modifier.levels=1
                    modifier.render_levels=1
                    changes.append(dict(object=obj.name,modifier=modifier.name,
                                        rule='restore_existing_skinned_surface_subdivision',
                                        before=before,after=dict(levels=1,render_levels=1,
                                                                 show_viewport=True,show_render=True)))

    def change(material, node, socket_name, value, rule):
        socket = node.inputs.get(socket_name)
        if socket is None or socket.is_linked: return False
        before = socket.default_value
        if isinstance(before,(int,float)):
            before=float(before)
            if abs(before-value)<1e-8:return False
        else:before=list(before)
        socket.default_value=value
        changes.append(dict(material=material.name,node=node.name,input=socket_name,
                            rule=rule,before=before,after=value))
        return True

    for material in sorted(materials,key=lambda m:m.name):
        role=roles[material]
        nodes=active_nodes(material)
        top=[n for tree,n in nodes if tree==material.node_tree]
        active_principled=[n for n in top if n.type=='BSDF_PRINCIPLED']
        item=dict(material=material.name,role=role,active_node_count=len(nodes),
                  modified=False)
        before_count=len(changes)
        if role=='skin' and opts['skin_material']:
            # This first candidate supports only explicit top-level Principled
            # skin. Unknown nested shader families are audited, never replaced.
            if not active_principled:
                skipped.append(dict(material=material.name,reason='skin_shader_needs_adapter'))
            for p in active_principled:
                rough=p.inputs['Roughness']
                if not rough.is_linked and rough.default_value < opts['skin_roughness_floor']:
                    change(material,p,'Roughness',opts['skin_roughness_floor'],'bounded_skin_roughness')
            for n in top:
                if n.type=='GROUP' and n.node_tree and 'daz top coat' in n.node_tree.name.lower():
                    rough=n.inputs.get('Roughness')
                    if rough and not rough.is_linked and rough.default_value < opts['skin_coat_floor']:
                        change(material,n,'Roughness',opts['skin_coat_floor'],'remove_mirror_skin_coat')
            # Retain imported SSS/translucency, albedo, normal and specular maps.
            # No fake pore texture, generic SSS replacement or skin recoloring.
        elif role=='hair' and opts['hair_material']:
            opacity=[n for _,n in nodes if n.type=='TEX_IMAGE' and n.image
                     and re.search(r'opacity|cutout|_op($|[ .])',n.image.name.lower())]
            if opacity:
                # On layered imported card materials, soften excessive relief
                # without changing card opacity, density, texture or silhouette.
                for n in top:
                    if n.type=='BUMP' and n.inputs['Height'].is_linked:
                        s=n.inputs['Strength']
                        if not s.is_linked and s.default_value > 0.75:
                            change(material,n,'Strength',s.default_value*opts['hair_bump_gain'],
                                   'reduce_strong_hair_card_bump')
                for p in active_principled:
                    s=p.inputs.get('Anisotropic')
                    if s and not s.is_linked and s.default_value==0:
                        change(material,p,'Anisotropic',0.35,'hair_card_directional_highlight_candidate')
            else:
                skipped.append(dict(material=material.name,reason='hair_without_recognized_active_cutout_preserved'))
        item['modified']=len(changes)>before_count
        report['materials'].append(item)
    report['status']='applied_candidate' if changes else 'audited_no_applicable_changes'
    return report
