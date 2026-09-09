"""Weighted lower-garment adaptation and evaluated-surface intersection audit."""
import math
import time

VERSION = 'garment_guard_v1'
POLICY = {'version': VERSION, 'enabled': True, 'unknown_policy': 'needs_review',
          'intersection_policy': 'reject_any_surface_crossing', 'all_frames': True,
          'max_rotation_degrees': 50, 'max_frame_rotation_degrees': 8}
GARMENT_WORDS = ('skirt', 'dress', 'gown', 'robe', 'kimono', '裙', '袍')


def garment_name(name):
    return any(word in name.lower() for word in GARMENT_WORDS)


def discover(target, meshes, semantic):
    used = set()
    for obj in meshes:
        for vertex in obj.data.vertices:
            for group in vertex.groups:
                if group.weight > .001:
                    used.add(obj.vertex_groups[group.group].name)
    cloth = {n for n in used if garment_name(n) and n in target.data.bones}
    thighs = [semantic.get(k) for k in ('leftupleg', 'rightupleg')]
    thighs = [target.data.bones[n] for n in thighs if n in target.data.bones]
    # The existing semantic mapper supports native, Mixamo and deform rigs.
    legs = set()
    for thigh in thighs:
        legs.add(thigh.name)
        legs.update(b.name for b in thigh.children_recursive)
    roots = {}
    failures = []
    for name in sorted(cloth):
        bone = target.data.bones[name]
        while bone.parent and garment_name(bone.parent.name):
            bone = bone.parent
        if bone.name in roots:
            continue
        if not bone.parent or len(thighs) != 2:
            failures.append('missing_pelvis_or_thigh_mapping:' + bone.name)
            continue
        pb = target.pose.bones[bone.name]
        if pb.constraints:
            failures.append('constrained_garment_root:' + bone.name)
            continue
        if pb.matrix_basis.to_quaternion().angle > 1e-4 or pb.location.length > 1e-5 or any(abs(s-1)>1e-4 for s in pb.scale):
            failures.append('non_neutral_garment_root_requires_adapter:' + bone.name)
            continue
        if target.animation_data and any(
            f'pose.bones["{bone.name}"]' in driver.data_path
            for driver in target.animation_data.drivers
        ):
            failures.append('driven_garment_root:' + bone.name)
            continue
        thigh = min(thighs, key=lambda t: (t.head_local-bone.head_local).length)
        roots[bone.name] = thigh.name
    labels = [obj.name for obj in meshes if garment_name(obj.name)]
    labels += [m.name for obj in meshes for m in obj.data.materials if m and garment_name(m.name)]
    if labels and not cloth:
        failures.append('named_garment_without_weighted_garment_bones')
    return {'roots': roots, 'cloth_groups': sorted(cloth), 'leg_groups': sorted(legs),
            'labels': labels, 'failures': sorted(set(failures))}


def adapt(target, discovery, frame_count):
    import bpy
    from mathutils import Quaternion
    peaks = {n: 0.0 for n in discovery['roots']}
    previous = {}
    velocity = 0.0
    clipped = []
    for frame in range(1, frame_count+1):
        bpy.context.scene.frame_set(frame)
        for name, thigh_name in discovery['roots'].items():
            pb = target.pose.bones[name]
            parent, thigh = pb.parent, target.pose.bones[thigh_name]
            rest = parent.bone.matrix_local.to_3x3().inverted() @ (thigh.bone.tail_local-thigh.bone.head_local)
            posed = parent.matrix.to_3x3().inverted() @ (thigh.tail-thigh.head)
            delta = rest.rotation_difference(posed)
            if delta.angle > math.radians(50):
                clipped.append(frame)
                delta = Quaternion(delta.axis, math.radians(50))
            relative = (parent.bone.matrix_local.inverted() @ pb.bone.matrix_local).to_quaternion()
            rotation = relative.inverted() @ delta @ relative
            if name in previous:
                rotation.make_compatible(previous[name])
                velocity = max(velocity, math.degrees(previous[name].rotation_difference(rotation).angle))
            previous[name] = rotation.copy()
            pb.rotation_mode = 'QUATERNION'
            pb.rotation_quaternion = rotation
            pb.keyframe_insert(data_path='rotation_quaternion', frame=frame, group='Garment clearance')
            peaks[name] = max(peaks[name], math.degrees(delta.angle))
        bpy.context.view_layer.update()
    return {'root_to_thigh': discovery['roots'], 'peak_degrees': peaks,
            'max_frame_rotation_degrees': velocity, 'clipped_frames': sorted(set(clipped))}


def surface_qc(meshes, discovery, frames):
    """Evaluate the final render's modifier visibility and subdivision budget."""
    import bpy
    saved = []
    try:
        for obj in meshes:
            for modifier in obj.modifiers:
                if modifier.type == 'NODES' and modifier.show_render:
                    raise RuntimeError('render-dependent geometry nodes require a garment adapter')
                values = {'show_viewport': modifier.show_viewport}
                if modifier.type in {'SUBSURF', 'MULTIRES'}:
                    values['levels'] = modifier.levels
                saved.append((modifier, values))
                modifier.show_viewport = modifier.show_render
                if modifier.type == 'SUBSURF':
                    modifier.levels = min(modifier.render_levels, int(bpy.context.scene.get('rm_subdivision_cap',2)))
                elif modifier.type == 'MULTIRES':
                    modifier.levels = modifier.render_levels
        bpy.context.view_layer.update()
        return _surface_qc(meshes, discovery, frames)
    finally:
        for modifier, values in reversed(saved):
            for name, value in values.items():
                setattr(modifier, name, value)
        bpy.context.view_layer.update()


def _surface_qc(meshes, discovery, frames):
    """Audit intersections on evaluated (skinned and modified) garment surfaces.

    Pair identity is retained for diagnosis; this is not a signed-volume test.
    A separate reviewed coverage contract is required for unknown garments.
    """
    import bpy
    from mathutils.bvhtree import BVHTree
    from mathutils.geometry import intersect_ray_tri, area_tri
    started = time.monotonic()
    cloth_groups, leg_groups = set(discovery['cloth_groups']), set(discovery['leg_groups'])
    candidates = [obj for obj in meshes if any(g.name in cloth_groups | leg_groups for g in obj.vertex_groups)]
    topology = {}
    measurements = []
    for frame in frames:
        bpy.context.scene.frame_set(frame)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        positions, cloth_faces, obstacle_faces = [], [], []
        for obj in candidates:
            evaluated = obj.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
            try:
                if obj.name not in topology:
                    cg = {g.index for g in obj.vertex_groups if g.name in cloth_groups}
                    lg = {g.index for g in obj.vertex_groups if g.name in leg_groups}
                    cweights = [sum(g.weight for g in v.groups if g.group in cg) for v in mesh.vertices]
                    lweights = [sum(g.weight for g in v.groups if g.group in lg) for v in mesh.vertices]
                    mesh.calc_loop_triangles()
                    cf, of = [], []
                    for tri in mesh.loop_triangles:
                        indices = tuple(tri.vertices)
                        if min(cweights[i] for i in indices) > .1:
                            cf.append(indices)
                        elif max(cweights[i] for i in indices) < .001 and min(lweights[i] for i in indices) > .25:
                            of.append(indices)
                    topology[obj.name] = (len(mesh.vertices), cf, of)
                count, cf, of = topology[obj.name]
                if count != len(mesh.vertices):
                    raise RuntimeError('garment audit requires stable evaluated topology: ' + obj.name)
                offset = len(positions)
                positions.extend(evaluated.matrix_world @ v.co for v in mesh.vertices)
                cloth_faces.extend(tuple(offset+i for i in tri) for tri in cf)
                obstacle_faces.extend(tuple(offset+i for i in tri) for tri in of)
            finally:
                evaluated.to_mesh_clear()
        hits = []
        if cloth_faces and obstacle_faces:
            cloth_tree = BVHTree.FromPolygons(positions, cloth_faces, all_triangles=True)
            obstacle_tree = BVHTree.FromPolygons(positions, obstacle_faces, all_triangles=True)
            pairs = cloth_tree.overlap(obstacle_tree)
            # BVH overlap is a broad phase. Count only interior segment/triangle
            # crossings, excluding shared endpoints and coplanar contacts.
            for a, b in pairs:
                ca = [positions[i] for i in cloth_faces[a]]
                ob = [positions[i] for i in obstacle_faces[b]]
                crossed = False
                for edges, triangle in ((ca, ob), (ob, ca)):
                    for i in range(3):
                        start, end = edges[i], edges[(i+1)%3]
                        direction = end-start
                        if direction.length < 1e-8:
                            continue
                        hit = intersect_ray_tri(*triangle, direction, start, True)
                        if hit is not None:
                            t = (hit-start).dot(direction)/direction.length_squared
                            if 1e-5 < t < 1-1e-5:
                                crossed = True
                                break
                    if crossed:
                        break
                if crossed:
                    hits.append((a,b))
        affected = {a for a,b in hits}
        area = sum(area_tri(*(positions[i] for i in cloth_faces[a])) for a in affected)
        total_area = sum(area_tri(*(positions[i] for i in tri)) for tri in cloth_faces)
        measurements.append({'frame': frame, 'intersecting_cloth_triangles': len({a for a,b in hits}),
                             'intersection_pairs': len(hits), 'cloth_triangles': len(cloth_faces),
                             'obstacle_triangles': len(obstacle_faces),
                             'intersecting_area_fraction': area/max(total_area,1e-12)})
    return {'method': 'evaluated_triangle_surface_overlap', 'frames': measurements,
            'elapsed_seconds': time.monotonic()-started}


def run_guard(target, meshes, semantic, frame_count):
    """Fail closed on unidentified coverage, unsupported rigs, or crossings."""
    started = time.monotonic()
    discovery = discover(target, meshes, semantic)
    report = {'version': VERSION, 'policy': POLICY, 'status': 'needs_review',
              'discovery': discovery, 'expected_frame_count': frame_count,
              'failures': list(discovery['failures'])}
    if not discovery['roots']:
        report['failures'].append('unclassified_lower_garment_coverage')
    if report['failures']:
        report['elapsed_seconds'] = time.monotonic()-started
        return report
    report['adaptation'] = adapt(target, discovery, frame_count)
    report['surface_qc'] = surface_qc(meshes, discovery, list(range(1,frame_count+1)))
    bad = [r['frame'] for r in report['surface_qc']['frames'] if r['intersection_pairs']]
    empty = [r['frame'] for r in report['surface_qc']['frames']
             if not r['cloth_triangles'] or not r['obstacle_triangles']]
    if empty:
        report['failures'].append('missing_evaluated_garment_or_obstacle_geometry')
    if bad:
        report['failures'].append('garment_surface_crossing_requires_review')
    if report['adaptation']['clipped_frames']:
        report['failures'].append('garment_rotation_limit_reached')
    if report['adaptation']['max_frame_rotation_degrees'] > POLICY['max_frame_rotation_degrees']:
        report['failures'].append('garment_rotation_discontinuity')
    report['problem_frames'] = bad
    report['status'] = 'passed' if not report['failures'] else 'needs_review'
    report['elapsed_seconds'] = time.monotonic()-started
    return report


def validate_report(report, frame_count):
    """Shared worker/core contract rejecting stale, partial and inconsistent passes."""
    if not isinstance(report, dict) or report.get('version') != VERSION or report.get('policy') != POLICY:
        raise RuntimeError('GARMENT_QC: missing or incompatible garment report')
    if report.get('status') != 'passed' or report.get('failures') or report.get('expected_frame_count') != frame_count:
        raise RuntimeError('GARMENT_QC: garment needs review: ' + str(report.get('failures')))
    rows = report.get('surface_qc', {}).get('frames', [])
    if [r.get('frame') for r in rows] != list(range(1,frame_count+1)):
        raise RuntimeError('GARMENT_QC: incomplete frame coverage')
    if any(r.get('intersection_pairs') != 0 or r.get('cloth_triangles',0) <= 0
           or r.get('obstacle_triangles',0) <= 0 for r in rows):
        raise RuntimeError('GARMENT_QC: nonzero intersections or missing geometry')
    adaptation = report.get('adaptation', {})
    if not adaptation.get('root_to_thigh') or adaptation.get('clipped_frames') != []:
        raise RuntimeError('GARMENT_QC: missing or limited garment adaptation')
    velocity = adaptation.get('max_frame_rotation_degrees', float('inf'))
    if not math.isfinite(velocity) or velocity > POLICY['max_frame_rotation_degrees']:
        raise RuntimeError('GARMENT_QC: garment rotation discontinuity')
