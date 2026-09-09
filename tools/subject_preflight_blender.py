"""Isolated human-level audit; CPU diagnostic images are never production output."""
import argparse,importlib.util,json,math,os,sys,time,traceback
from pathlib import Path
import bpy
from mathutils import Vector

VERSION='subject_preflight_v1'
def main():
 p=argparse.ArgumentParser();p.add_argument('--subject-json',required=True);p.add_argument('--output',required=True);p.add_argument('--example',required=True);a=p.parse_args(sys.argv[sys.argv.index('--')+1:])
 row=json.loads(Path(a.subject_json).read_text());out=Path(a.output);out.mkdir(parents=True,exist_ok=True);started=time.monotonic();checks={};record={'version':VERSION,'subject_id':row['subject_id'],'asset_path':row['asset_path'],'historical_status':row['status'],'checks':checks,'production_eligible':False,'diagnostic_only':True,'previews':[]}
 def save():
  record['elapsed_seconds']=time.monotonic()-started;(out/'report.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
 def stage(name,fn):
  save()
  try:
   value=fn();checks[name]={'status':'passed','result':value};save();return value
  except Exception as e:
   checks[name]={'status':'failed','error':str(e),'error_type':type(e).__name__};save();return None
 try:
  before=Path(row['asset_path']).stat();record['source_stat']={'bytes':before.st_size,'mtime_ns':before.st_mtime_ns}
  try:bpy.ops.wm.open_mainfile(filepath=row['asset_path'],load_ui=False)
  except RuntimeError as e:
   if Path(bpy.data.filepath).resolve()!=Path(row['asset_path']).resolve():raise
   record['load_warning']=str(e)
  checks['load']={'status':'passed' if not record.get('load_warning') else 'warning'}
  sys.path.insert(0,str(Path(os.environ['DATA_ROOT'])/'tools'))
  import procedural_reference_production as core
  from production_quality import apply_quality
  from production_quality.garments import run_guard,validate_report
  helper=core.load_helper();target=helper.target_armature([o for o in bpy.context.scene.objects if o.type=='ARMATURE'])
  stage('hair_variant',lambda:core.align_hair_variant_visibility(helper,target))
  meshes=core.character_render_meshes(helper,target);semantic=helper.semantic_bone_map(target)
  required=['torso','head','foot_ik.L','foot_ik.R','thigh_ik_target.L','thigh_ik_target.R','hand_ik.L','hand_ik.R','upper_arm_ik_target.L','upper_arm_ik_target.R']
  missing=sorted(set(required)-set(target.pose.bones.keys()))
  checks['structure']={'status':'passed' if not missing else 'needs_adapter','target_armature':target.name,'bone_count':len(target.data.bones),'visible_mesh_count':len(meshes),'semantic_coverage':len(semantic),'semantic_map':semantic,'missing_controls':missing}
  stage('active_textures',lambda:helper.repair_character_textures(meshes))
  stage('alpha_materials',lambda:core.normalize_matting_alpha(meshes));stage('outline_cleanup',lambda:core.neutralize_stylized_edges(meshes));stage('quality_materials',lambda:apply_quality(meshes,{}))
  for obj in meshes:
   for mod in obj.modifiers:
    if mod.type in {'SUBSURF','MULTIRES'}:
     mod.levels=min(mod.levels,2);mod.render_levels=min(mod.render_levels,2)
  bpy.context.scene.frame_set(1)
  bounds=stage('static_bounds',lambda:[list(v) for v in helper.evaluated_bounds(meshes,[1],'fullbody')])
  if bounds and (not all(math.isfinite(v) for point in bounds for v in point) or max(bounds[1][i]-bounds[0][i] for i in range(3))<=1e-6):checks['static_bounds']={'status':'failed','error':'non-finite or empty bounds'}
  dynamic=False;frame_count=120
  if not missing and checks['active_textures']['status']=='passed':
   example=json.loads(Path(a.example).read_text());core.ACTION_PARAMS=example['action_params'];core.EXPANDED_PRESET_PATH=str(Path(os.environ['DATA_ROOT'])/'tools/expanded_motion_presets_v4_expanded.py')
   motion=stage('representative_motion',lambda:core.generate_motion(helper,target,[i/(frame_count-1) for i in range(frame_count)],'expanded_preset',meshes))
   if motion:
    dynamic=True
    stage('hair_motion',lambda:core.add_hair_secondary_motion(target,meshes,frame_count,'expanded_preset'))
    garment=stage('garment',lambda:run_guard(target,meshes,semantic,frame_count))
    if garment:
     try:validate_report(garment,frame_count)
     except Exception as e:checks['garment']['status']='needs_review';checks['garment']['error']=str(e)
    stage('geometry_motion',lambda:core.geometry_motion_qc(helper,target,meshes,list(range(1,frame_count+1)),motion['character_height']))
    stage('limb_deformation',lambda:core.limb_deformation_qc(target,list(range(1,frame_count+1)),motion['character_height']))
  else:checks['representative_motion']={'status':'skipped','reason':'missing target-native IK controls or unresolved active textures'}
  # Independent static/representative thumbnails: reduced resolution/sampling,
  # separate camera, no bypass of any production result or DONE marker.
  scene=bpy.context.scene
  for obj in list(scene.objects):
   if obj.type in {'LIGHT','CAMERA'}:obj.hide_render=True
   elif obj.type=='MESH' and obj not in meshes:obj.hide_render=True
  scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=2;scene.cycles.use_denoising=False;scene.cycles.use_adaptive_sampling=False;scene.cycles.max_bounces=4;scene.cycles.transparent_max_bounces=12;scene.cycles.time_limit=12
  scene.render.resolution_x=320;scene.render.resolution_y=180;scene.render.resolution_percentage=100;scene.render.film_transparent=True;scene.render.use_compositing=False;scene.render.use_sequencer=False;scene.render.image_settings.media_type='IMAGE';scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGBA';scene.render.image_settings.color_depth='8';scene.render.use_persistent_data=True;scene.render.use_motion_blur=False
  scene.view_settings.view_transform='AgX';scene.view_settings.look='None';scene.view_settings.exposure=0;scene.view_settings.gamma=1
  world=bpy.data.worlds.new('SubjectAuditWorld');world.use_nodes=True;world.node_tree.nodes.get('Background').inputs['Color'].default_value=(.5,.5,.5,1);world.node_tree.nodes.get('Background').inputs['Strength'].default_value=.6;scene.world=world
  lightdata=bpy.data.lights.new('SubjectAuditSun','SUN');lightdata.energy=2;light=bpy.data.objects.new('SubjectAuditSun',lightdata);scene.collection.objects.link(light);light.rotation_euler=(.5,-.5,-.3)
  camera=bpy.data.objects.new('SubjectAuditCamera',bpy.data.cameras.new('SubjectAuditCamera'));scene.collection.objects.link(camera);camera.data.type='ORTHO';scene.camera=camera
  for name,framing,angle,frame in [('fullbody_front','fullbody',0,1),('halfbody_side','halfbody',45,60 if dynamic else 1),('hair_front','closeup_hair',0,120 if dynamic else 1)]:
   def render():
    scene.frame_set(frame);low,high=helper.evaluated_bounds(meshes,[frame],framing);extent=high-low;center=(low+high)/2;distance=max(4,extent.length*2);rad=math.radians(angle);camera.location=center+Vector((math.sin(rad)*distance,-math.cos(rad)*distance,0));helper.look_at(camera,center);camera.data.ortho_scale=max(extent.x,extent.y,extent.z*16/9)*1.18;camera.data.clip_end=max(1000,distance*10);camera.data.clip_start=max(.001,distance/10000)
    path=out/(name+'.png');scene.render.filepath=str(path);bpy.ops.render.render(write_still=True);return {'file':path.name,'frame':frame,'framing':framing,'camera_angle':angle,'size':[320,180],'samples':2,'device':'CPU','diagnostic_only':True}
   value=stage('preview_'+name,render)
   if value:record['previews'].append(value)
  bad=[k for k,v in checks.items() if v['status'] not in ['passed','skipped']]
  # Some checks return a structured QC result without throwing.
  for k in ['geometry_motion','limb_deformation','hair_motion']:
   result=checks.get(k,{}).get('result') or {}
   if result.get('status') in ['failed','needs_review'] or result.get('hair_motion_qc_status')=='needs_hair_adapter':bad.append(k)
  record['blocking_checks']=sorted(set(bad))
  if checks['active_textures']['status']!='passed':record['status']='needs_asset_repair'
  elif missing:record['status']='needs_adapter'
  elif bad or record.get('load_warning') or len(record['previews'])!=3:record['status']='needs_quality_review'
  else:record['status']='ready_for_task_preflight'
  after=Path(row['asset_path']).stat();record['source_unchanged']=before.st_size==after.st_size and before.st_mtime_ns==after.st_mtime_ns
  if not record['source_unchanged']:raise RuntimeError('Source file changed during audit')
 except Exception as e:record['status']='inspection_failed';record['error']=str(e);record['traceback']=traceback.format_exc()
 finally:save()
if __name__=='__main__':main()
