"""Production configuration and the archived round-one appearance policy."""
import hashlib
import json
from pathlib import Path

from .materials import apply_quality, adapt_lighting, background_statistics
from .garments import POLICY as GARMENT_POLICY

HERE = Path(__file__).resolve().parent


def load_setting():
    path = HERE / 'setting.json'
    setting = json.loads(path.read_text())
    if setting['resolution'] != [1920, 1080] or setting['fps'] != 30:
        raise ValueError('Production policy requires 1920x1080 at 30 fps')
    if setting.get('garment_guard') != GARMENT_POLICY:
        raise ValueError('Production requires the current fail-closed garment policy')
    s = setting['sampling']
    if not 1 <= s['min_samples'] <= s['samples'] <= 4096 or not 0 <= s['threshold'] <= 1:
        raise ValueError('Invalid production sample budget')
    setting['setting_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    return setting


def job_settings():
    setting = load_setting()
    sampling = setting['sampling']
    return {
        'resolution_x': setting['resolution'][0], 'resolution_y': setting['resolution'][1],
        'samples': sampling['samples'], 'cycles_sample_cap': sampling['samples'],
        'cycles_denoising': sampling['denoising'], 'quality_sampling': sampling,
        'quality_options': setting['quality_options'],
        'pixel_filter_width': setting['pixel_filter_width'],
        'subdivision_cap': setting['subdivision_cap'],
        'production_setting': setting,
    }


def configure_render(helper, job, detail_report, prepass):
    """Apply lights once after setup, with full samples only for final Cycles."""
    import bpy
    original = helper.setup_render

    def setup_render(*args, **kwargs):
        lighting, sampling = {}, {}
        def once(scene, *unused):
            if lighting:
                return
            lighting.update(adapt_lighting(scene, job['background_light_statistics']))
            if not prepass:
                s = job['quality_sampling']
                scene.cycles.samples = s['samples']
                scene.cycles.adaptive_min_samples = s['min_samples']
                scene.cycles.adaptive_threshold = s['threshold']
                scene.cycles.use_denoising = s['denoising']
                sampling.update(s)
        bpy.app.handlers.render_pre.append(once)
        try:
            report = original(*args, **kwargs)
        finally:
            bpy.app.handlers.render_pre.remove(once)
        report.update(quality_detail=detail_report, quality_lighting=lighting,
                      production_setting=job['production_setting'])
        if sampling:
            report.update(cycles_max_samples=sampling['samples'], cycles_min_samples=sampling['min_samples'],
                          cycles_adaptive_threshold=sampling['threshold'], cycles_denoising=sampling['denoising'])
        report['quality_source_hashes'] = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in HERE.iterdir() if p.suffix in {'.py', '.json'}
        }
        return report
    helper.setup_render = setup_render
