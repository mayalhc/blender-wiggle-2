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
import tempfile

import bpy
from mathutils import Matrix

CACHE_EXT = ".wcache"

# In-memory layer on top of the disk files, so playback doesn't touch the
# disk every frame (Blender's own point cache works the same way):
#   _DIR_CACHE : (raw dir setting, .blend path) -> resolved absolute dir
#   _INDEX     : (dir, object) -> set of frame numbers that exist on disk
#   _MEM       : (dir, object) -> {frame: data} already loaded or saved
_DIR_CACHE = {}
_INDEX = {}
_MEM = {}


def _safe_name(name):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)


def get_cache_dir(scene):
    raw = getattr(scene, "wiggle_cache_dir", "//wiggle2_cache/") or "//wiggle2_cache/"
    key = (raw, bpy.data.filepath)
    path = _DIR_CACHE.get(key)
    if path is None:
        if raw.startswith("//") and not bpy.data.filepath:
            # Unsaved .blend: "//" has no folder to be relative to, so it
            # would resolve against Blender's working directory (on Windows
            # often inside Program Files, which isn't writable), and every
            # cache write would fail silently. Use Blender's temp folder
            # until the file is saved.
            base = bpy.app.tempdir or tempfile.gettempdir()
            path = os.path.join(base, raw[2:] or "wiggle2_cache")
        else:
            path = bpy.path.abspath(raw)
        path = os.path.normpath(path)
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass
        _DIR_CACHE[key] = path
    return path


def _key(scene, ob_name):
    return (get_cache_dir(scene), _safe_name(ob_name))


def frame_path(scene, ob_name, frame):
    return os.path.join(get_cache_dir(scene), f"{_safe_name(ob_name)}_{frame:06d}{CACHE_EXT}")


def _frames_index(scene, ob_name):
    """Frame numbers cached for this object. The folder is listed once and
    the set is then kept up to date by save_frame/clear_cache."""
    key = _key(scene, ob_name)
    idx = _INDEX.get(key)
    if idx is None:
        d, safe = key
        prefix = safe + "_"
        idx = set()
        try:
            names = os.listdir(d)
        except OSError:
            names = []
        for fn in names:
            if fn.startswith(prefix) and fn.endswith(CACHE_EXT):
                num = fn[len(prefix):-len(CACHE_EXT)]
                if num.isdigit():
                    idx.add(int(num))
        _INDEX[key] = idx
    return idx


def has_frame(scene, ob_name, frame):
    return frame in _frames_index(scene, ob_name)


def cached_frame_count(scene, ob_name):
    return len(_frames_index(scene, ob_name))


# v3 stores the complete bone state (collision/sticky + adaptive damping);
# v2 files (the earlier 7-field format) are still accepted on load.
CACHE_VERSION = 3


def _vec(v):
    return tuple(float(x) for x in v)


def _mat_rows(m):
    # bw.matrix is a 16-float MATRIX property, so Blender returns a
    # mathutils.Matrix. tuple(m) would give 4 mathutils.Vector rows, which
    # pickle can't serialize (the bug that made every cache write fail).
    # Store plain float rows instead.
    return tuple(tuple(float(x) for x in row) for row in m)


def save_frame(scene, ob, wo, frame):
    data = {"__version__": CACHE_VERSION}
    for wb in wo.list:
        b = ob.pose.bones.get(wb.name)
        if not b:
            continue
        bw = b.wiggle
        data[wb.name] = {
            "position": _vec(bw.position),
            "position_last": _vec(bw.position_last),
            "velocity": _vec(bw.velocity),
            "position_head": _vec(bw.position_head),
            "position_last_head": _vec(bw.position_last_head),
            "velocity_head": _vec(bw.velocity_head),
            "collision_point": _vec(getattr(bw, "collision_point", (0.0, 0.0, 0.0))),
            "collision_normal": _vec(getattr(bw, "collision_normal", (0.0, 0.0, 0.0))),
            "collision_point_head": _vec(getattr(bw, "collision_point_head", (0.0, 0.0, 0.0))),
            "collision_normal_head": _vec(getattr(bw, "collision_normal_head", (0.0, 0.0, 0.0))),
            "adaptive_damp_mod": float(getattr(bw, "adaptive_damp_mod", 0.0)),
            "matrix": _mat_rows(bw.matrix),
        }
    if len(data) <= 1:
        return False
    key = _key(scene, ob.name)
    path = frame_path(scene, ob.name, frame)
    tmp = path + ".tmp"
    try:
        # Write to a temp file and rename, so a failed write can never
        # leave a half-written cache file behind.
        with open(tmp, "wb") as f:
            pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
    except Exception as e:
        print(f"Wiggle2 cache: could not write frame {frame} for '{ob.name}': {e}")
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    _frames_index(scene, ob.name).add(frame)
    _MEM.setdefault(key, {})[frame] = data
    return True


def load_frame(scene, ob, wo):
    """Loads the cached frame for the CURRENT scene frame onto `ob`'s wiggle
    bones. Returns True if cached data existed and was applied."""
    frame = scene.frame_current
    if not has_frame(scene, ob.name, frame):
        return False
    key = _key(scene, ob.name)
    mem = _MEM.setdefault(key, {})
    data = mem.get(frame)
    if data is None:
        path = frame_path(scene, ob.name, frame)
        try:
            with open(path, "rb") as f:
                data = pickle.load(f)
            if not isinstance(data, dict) or data.get("__version__") not in (2, CACHE_VERSION):
                raise ValueError("old or invalid cache file")
        except Exception:
            # Broken/old file (e.g. written by the buggy version): drop it
            # so this frame simply re-simulates and gets re-cached.
            _frames_index(scene, ob.name).discard(frame)
            try:
                os.remove(path)
            except OSError:
                pass
            return False
        mem[frame] = data

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
        # A 16-float MATRIX property only accepts a flat list of 16 floats
        # (column-major, same as wiggle_2.flatten()); assigning a Matrix
        # object raises and would abort the whole physics handler.
        m = Matrix(d["matrix"])
        bw.matrix = [m[j][i] for i in range(4) for j in range(4)]

        # v3 fields: only present in newer cache files; older v2 files
        # simply leave the current values untouched.
        for _f in ("collision_point", "collision_normal",
                   "collision_point_head", "collision_normal_head"):
            if _f in d and hasattr(bw, _f):
                setattr(bw, _f, d[_f])
        if "adaptive_damp_mod" in d and hasattr(bw, "adaptive_damp_mod"):
            bw.adaptive_damp_mod = float(d["adaptive_damp_mod"])
    return True


def clear_cache(scene, ob_name=None):
    d = get_cache_dir(scene)
    # Forget in-memory data for this folder (all objects, or just one).
    safe = _safe_name(ob_name) if ob_name else None
    for store in (_INDEX, _MEM):
        for k in [k for k in store if k[0] == d and (safe is None or k[1] == safe)]:
            del store[k]
    if not os.path.isdir(d):
        return 0
    prefix = (safe + "_") if safe else None
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
