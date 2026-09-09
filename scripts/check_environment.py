"""Check a new Linux runtime before any workspace or render is created."""
import json,os,platform,shutil,subprocess,sys
from pathlib import Path
if platform.system()!='Linux' or sys.version_info<(3,10):raise RuntimeError('Validated target: Linux with host Python >=3.10')
for name in ['ffmpeg','ffprobe','zstd']:
 if not shutil.which(name):raise RuntimeError('Missing executable: '+name)
blender=Path(os.environ['BLENDER_BIN'])
if not blender.is_file():raise RuntimeError('BLENDER_BIN must point to the actual Blender executable')
expression='''import bpy,json
assert bpy.app.version == (5,0,1), 'This release pins Blender 5.0.1'
p=bpy.context.preferences.addons['cycles'].preferences
found=[]
for backend in ['OPTIX','CUDA']:
 try:
  p.compute_device_type=backend;p.get_devices()
  devices=[{'name':d.name,'type':d.type} for d in p.devices if d.type==backend]
  if devices:found.append({'backend':backend,'devices':devices})
 except Exception:pass
assert found, 'No supported NVIDIA Cycles device found; check host driver/container GPU access'
print('VIDEOMATTING_RUNTIME='+json.dumps({'blender':bpy.app.version_string,'gpu_backends':found}))
'''
r=subprocess.run([str(blender),'--background','--factory-startup','--python-exit-code','1','--python-expr',expression],capture_output=True,text=True)
if r.returncode:raise RuntimeError(r.stdout+'\n'+r.stderr)
lines=[x for x in r.stdout.splitlines() if x.startswith('VIDEOMATTING_RUNTIME=')]
if not lines:raise RuntimeError('Blender did not return the runtime probe')
print(lines[-1].split('=',1)[1])
