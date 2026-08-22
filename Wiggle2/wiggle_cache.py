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

API contract ([Bug fix] the old version did not match wiggle_post's):
  save_frame() : pickles the ALREADY-serialized plain dict produced by
                 wiggle_2._build_serializable_wo(), VERBATIM (all fields).
  load_frame() : PURE READER - returns the plain dict, or None on miss/
                 corruption. Writing into PropertyGroups is the CALLER's
                 job (wiggle_2._restore_wo_from_dict).
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


def save_frame(scene, ob, wo_dict, frame):
    """Store the serialized per-bone record as-is.
    [Bug fix] The old version cherry-picked 7 fields, which dropped the
    collision/sticky state and would have raised KeyError('collision_point')
    the moment the dict-based restore path ran."""
    if not wo_dict:
        return False
    payload = {name: dict(fields) for name, fields in wo_dict.items()
               if isinstance(fields, dict)}
    if not payload:
        return False
    try:
        with open(frame_path(scene, ob.name, frame), "wb") as f:
            pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
        return True
    except OSError:
        return False


def load_frame(scene, ob_name, frame):
    """Pure reader: return the cached plain dict for (ob_name, frame),
    or None if missing/unreadable. Never writes into bpy structs itself -
    that duplication was exactly the bool-vs-dict API mismatch."""
    path = frame_path(scene, ob_name, frame)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            data = pickle.load(f)
    except Exception:
        # A corrupt cache file must never kill the frame_change handler;
        # None simply makes wiggle_post re-simulate the frame.
        return None
    return data if isinstance(data, dict) else None


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