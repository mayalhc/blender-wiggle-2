"""Disk point cache for Wiggle 2.

Scrubbing the timeline forces wiggle_post to re-simulate every frame from
scene.frame_start every time you jump backward (see the reset branch in
wiggle_2.wiggle_post) - fine for short clips, painful for long ones. This
module lets each frame's already-computed per-bone physics state
(position/velocity/matrix) be saved to disk and reloaded instead of
re-simulated, the same idea as Blender's built-in Cloth/Softbody point cache.

One file per (object, frame). Cache is invalidated manually (Clear Cache) -
there is no automatic dependency tracking of stiffness/damping/topology
changes, so if you tweak settings after baking, clear and re-bake.
"""
import os
import pickle

import bpy

CACHE_EXT = ".wcache"


def _safe_name(name):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)


def get_cache_dir(scene):
    raw = getattr(scene, "wiggle_cache_dir", "//wiggle2_cache/")
    path = bpy.path.abspath(raw)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def frame_path(scene, ob_name, frame):
    return os.path.join(get_cache_dir(scene), f"{_safe_name(ob_name)}_{frame:06d}{CACHE_EXT}")


def has_frame(scene, ob_name, frame):
    return os.path.exists(frame_path(scene, ob_name, frame))


def save_frame(scene, ob, wo, frame):
    data = {}
    for wb in wo.list:
        b = ob.pose.bones.get(wb.name)
        if not b:
            continue
        bw = b.wiggle
        data[wb.name] = {
            "position": tuple(bw.position),
            "position_last": tuple(bw.position_last),
            "velocity": tuple(bw.velocity),
            "position_head": tuple(bw.position_head),
            "position_last_head": tuple(bw.position_last_head),
            "velocity_head": tuple(bw.velocity_head),
            "matrix": tuple(bw.matrix),
        }
    if not data:
        return False
    try:
        with open(frame_path(scene, ob.name, frame), "wb") as f:
            pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
        return True
    except OSError:
        return False


def load_frame(scene, ob, wo):
    """Loads the cached frame for the CURRENT scene frame onto `ob`'s wiggle
    bones. Returns True if a cache file existed and was applied."""
    frame = scene.frame_current
    path = frame_path(scene, ob.name, frame)
    if not os.path.exists(path):
        return False
    try:
        with open(path, "rb") as f:
            data = pickle.load(f)
    except (OSError, pickle.PickleError, EOFError):
        return False

    for wb in wo.list:
        b = ob.pose.bones.get(wb.name)
        if not b or wb.name not in data:
            continue
        d = data[wb.name]
        bw = b.wiggle
        bw.position = d["position"]
        bw.position_last = d["position_last"]
        bw.velocity = d["velocity"]
        bw.position_head = d["position_head"]
        bw.position_last_head = d["position_last_head"]
        bw.velocity_head = d["velocity_head"]
        bw.matrix = d["matrix"]
    return True


def clear_cache(scene, ob_name=None):
    d = get_cache_dir(scene)
    if not os.path.isdir(d):
        return 0
    prefix = (_safe_name(ob_name) + "_") if ob_name else None
    removed = 0
    for fn in os.listdir(d):
        if not fn.endswith(CACHE_EXT):
            continue
        if prefix and not fn.startswith(prefix):
            continue
        try:
            os.remove(os.path.join(d, fn))
            removed += 1
        except OSError:
            pass
    return removed
