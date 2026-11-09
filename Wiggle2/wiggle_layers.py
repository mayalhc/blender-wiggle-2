import bpy
from bpy.app.handlers import persistent

# ============================================================
# wiggle_layers.py - Sim Mix Layer system (v5)
#
# Adding a layer immediately creates a real action + a dedicated NLA
# track and links them (the Base layer becomes "WGL_Base", each Sim
# layer becomes "WGL_Trk_<name>"). The Layer Weight slider actually
# cross-fades those NLA strips' influence, and Sim Mix separately
# controls live physics strength (pb.wiggle_influence).
#
# Bugs fixed previously (do not reintroduce these):
# [Bug-1 (rediscovered)] Assumed "static influence needs
#         use_animated_influence turned OFF to work" - this was exactly
#         backwards. Measured directly (same result on Blender 4.2 LTS,
#         5.0, 5.1, 5.2): with use_animated_influence off, Blender's
#         evaluator ignores strip.influence entirely (0 or 1, same
#         result) - only mute takes effect immediately. For influence to
#         actually apply, use_animated_influence must be turned ON with
#         one CONSTANT-interpolation keyframe holding the value. That is
#         what set_strip_influence() does.
# [Bug-2] sync_layers() used to force
#         pb.wiggle_stiff = (1-influence)*80.0 on every bone every frame
#         - this destroyed the user's own Stiff setting (including
#         presets). Do not add this back.
# [Bug-3] physics_strength calculation didn't multiply by Layer Weight
#         (active_layer.influence), so dropping Weight to 0 didn't stop
#         live physics.
# [Bug-4] wiggle_influence's default was registered as 0.0 - any bone
#         that just had wiggle turned on got zero physics until the next
#         sync. Must be 1.0.
# [Bug-5] The bake operator used to assign to
#         scene.wiggle.wiggle_mute, which doesn't exist, throwing an
#         AttributeError the moment you clicked Bake.
# ============================================================



class NoAutoKey:
    """Temporarily turn off Blender's Auto Keying while the addon itself
    changes bone transforms (physics, resets, layer syncing), so Blender
    never records those changes as keyframes. The user's setting is
    restored immediately afterwards."""
    def __init__(self, scene):
        self.ts = getattr(scene, "tool_settings", None) if scene else None
        self.prev = None

    def __enter__(self):
        if self.ts is not None and getattr(self.ts, "use_keyframe_insert_auto", False):
            self.prev = True
            try:
                self.ts.use_keyframe_insert_auto = False
            except Exception:
                self.prev = None
        return self

    def __exit__(self, *exc):
        if self.prev:
            try:
                self.ts.use_keyframe_insert_auto = True
            except Exception:
                pass
        return False

def set_strip_influence(strip, value):
    """Actually make an NLA strip's influence apply. Static (non-animated)
    influence is ignored by Blender's evaluator (verified by measurement),
    so this works around it by turning on use_animated_influence and
    inserting one CONSTANT keyframe for the value (holds flat across the
    whole strip)."""
    value = max(0.0, min(1.0, float(value)))

    # Full weight needs no keyframe at all: with use_animated_influence
    # OFF, Blender plays the strip at full strength (see Bug-1 above), so
    # a layer at 100% is just a plain strip. That keeps layers at 100% from
    # putting "Influence" keys in the Dope Sheet/timeline, which looked
    # like the animation was being keyed.
    if value >= 0.9999:
        if strip.use_animated_influence:
            strip.use_animated_influence = False
        for existing in list(strip.fcurves):
            if existing.data_path != 'influence':
                continue
            removed = False
            remover = getattr(strip.fcurves, "remove", None)
            if remover is not None:
                try:
                    remover(existing)
                    removed = True
                except Exception:
                    removed = False
            if not removed:
                while existing.keyframe_points:
                    existing.keyframe_points.remove(existing.keyframe_points[0])
                existing.update()
            break
        return

    # Below 100%: Blender only applies a custom strip influence through an
    # animated influence curve, so one CONSTANT key is genuinely needed.
    # Only flip the flag when needed: turning it on makes Blender create
    # the influence F-curve (and a key), so re-setting it every frame is
    # pointless churn.
    if not strip.use_animated_influence:
        strip.use_animated_influence = True
    fc = None
    for existing in strip.fcurves:
        if existing.data_path == 'influence':
            fc = existing
            break
    if fc is None:
        fc = strip.fcurves.new('influence')
    # Bug fix: snapshotting list(fc.keyframe_points) up front and then
    # removing them one at a time throws "RuntimeError: Keyframe not in
    # F-Curve" once there are 2+ keyframes - removing one invalidates
    # Blender's other keyframe-point wrapper references in that same
    # collection (internal array reshuffle), so calling remove() again
    # with a stale snapshot reference fails. Always re-fetch index 0 from
    # the live collection instead. This actually happened in practice
    # when this function touched a strip that already had multiple
    # animated-influence keyframes (e.g. adopting an existing NLA track
    # as Base).
    # sync_layers() calls this on EVERY frame change. It used to delete and
    # re-insert the key each time, which made a fresh (selected, yellow)
    # keyframe pop up in the timeline on every frame during playback and
    # looked exactly like auto-keying. If the single key is already right,
    # leave it alone.
    pts = fc.keyframe_points
    if (len(pts) == 1
            and abs(pts[0].co[0] - strip.frame_start) < 1e-4
            and abs(pts[0].co[1] - value) < 1e-6
            and pts[0].interpolation == 'CONSTANT'):
        return
    while fc.keyframe_points:
        fc.keyframe_points.remove(fc.keyframe_points[0])
    kp = fc.keyframe_points.insert(strip.frame_start, value)
    kp.interpolation = 'CONSTANT'
    # Not selected, so it doesn't stand out as a "new" key in the timeline.
    kp.select_control_point = False
    kp.select_left_handle = False
    kp.select_right_handle = False
    fc.update()


def _clear_action_keyframes(action, bone_names=None):
    """Clears keyframe F-curves. If bone_names is provided, only clears
    F-curves belonging to those specific bones, leaving other bones intact."""
    if bone_names is None:
        if hasattr(action, "fcurves"):
            for fc in list(action.fcurves):
                action.fcurves.remove(fc)
            return
        for layer in getattr(action, "layers", []):
            for strip in layer.strips:
                for cb in getattr(strip, "channelbags", []):
                    for fc in list(cb.fcurves):
                        cb.fcurves.remove(fc)
        return

    bone_set = set(bone_names)
    if hasattr(action, "fcurves"):
        for fc in list(action.fcurves):
            if any(f'["{b}"]' in fc.data_path for b in bone_set):
                action.fcurves.remove(fc)
        return
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for cb in getattr(strip, "channelbags", []):
                for fc in list(cb.fcurves):
                    if any(f'["{b}"]' in fc.data_path for b in bone_set):
                        cb.fcurves.remove(fc)


def _sync_strip_range(track, action):
    """Bug fix: a Sim layer starts out linked to a blank action, and at
    that moment the NLA strip's playback range gets locked to the
    action's (then-empty) frame_range (measured: action_frame_end froze
    at 1.0). Later, once real keyframes get baked into that action (say
    frames 1-20), the strip still only played back the frozen range and
    never showed the new content.

    Patching action_frame_start/end alone does not fix this - measured
    directly, Blender's NLA strip caches something internally that a
    plain property patch doesn't invalidate. The only reliable fix is to
    delete the strip and recreate it (blend_type/influence/extrapolation
    get restored by the caller right after)."""
    if not action or not track.strips:
        return next(iter(track.strips), None)
    strip = track.strips[0]
    try:
        a_start, a_end = action.frame_range
    except Exception:
        return strip
    if a_end <= a_start:
        a_end = a_start + 1
    if strip.action_frame_start == a_start and strip.action_frame_end == a_end:
        return strip
    blend_type = strip.blend_type
    # With animated influence off the strip plays at full strength, and
    # strip.influence may hold a stale value, so treat it as 1.0.
    influence = strip.influence if strip.use_animated_influence else 1.0
    track.strips.remove(strip)
    new_strip = track.strips.new(action.name, int(a_start), action)
    new_strip.blend_type = blend_type
    new_strip.extrapolation = 'HOLD'
    set_strip_influence(new_strip, influence)
    return new_strip


def _find_track_for_action(obj, action):
    """Find the first NLA track (including non-WGL_ ones) that contains a
    strip referencing this action."""
    if not action or not obj.animation_data:
        return None
    for track in obj.animation_data.nla_tracks:
        for strip in track.strips:
            if strip.action == action:
                return track
    return None


def ensure_layer_linked(obj, layer, is_first):
    """Make sure a layer has a real action and a dedicated NLA track
    linked, creating them immediately if not. The goal is for a newly
    added layer to be visible in the NLA editor right away."""
    if not obj.animation_data:
        obj.animation_data_create()

    action = bpy.data.actions.get(layer.action_name) if layer.action_name else None
    track = _find_track_for_action(obj, action) if action else None

    if is_first:
        existing_base = next((t for t in obj.animation_data.nla_tracks if t.name == "WGL_Base"), None)

        # Bug fix: if a "WGL_Base" track already exists with exactly one
        # strip that has a real action on it, that strip's action is the
        # source of truth - sync our cached layer.action_name to match
        # it instead of the other way around. This matters because a
        # user can (and normally would) repoint that strip to a
        # different action directly via the NLA editor's own Strip
        # panel; the previous logic below only ever trusted
        # layer.action_name and would forcibly overwrite the strip back
        # to whatever action was cached, silently undoing the user's
        # change on the very next sync (every frame during playback).
        if existing_base and len(existing_base.strips) == 1 and existing_base.strips[0].action:
            strip = existing_base.strips[0]
            if strip.action.name != layer.action_name:
                layer.action_name = strip.action.name
            action = strip.action
            track = existing_base
            strip.blend_type = 'REPLACE'
            strip.extrapolation = 'HOLD'
            track.name = "WGL_Base"
            if not layer.name or layer.name.startswith("Layer") or layer.name.startswith("Sim Layer"):
                layer.name = action.name
            return action, track

        # Base layer - link to whatever animation the user already has.
        # Bug fix: this used to only look for an existing animation the
        # very first time layer.action_name was completely empty. So if
        # a bad link (e.g. an empty action) got left over from earlier
        # testing, it never re-checked afterward and kept missing a real
        # NLA track (e.g. "Sections") that actually existed on the
        # object. Now it re-checks the object's real state (a track not
        # starting with "WGL_", or the active action) on every sync and
        # switches to it if different from what's currently linked.
        # Bug fix / safety: this candidate scan was meant to run only
        # once, the moment the Base layer is first created, to find an
        # animation the user already had. Instead it ran unconditionally
        # on every single sync (= every frame during playback!) and
        # switched tracks whenever the candidate differed. If the
        # armature had other NLA tracks unrelated to this addon (e.g.
        # several tracks the user was already cross-fading via animated
        # influence), this scan would pick one of them, absorb it as
        # "WGL_Base", and forcibly overwrite every strip in it with
        # blend_type=REPLACE + a flat influence value (wiping out the
        # user's own animated influence). Now it never re-scans once a
        # valid action is already linked (action is truthy) - it only
        # auto-detects the very first time, when nothing is linked yet.
        candidate_action, candidate_track = None, None
        if not action:
            for t in obj.animation_data.nla_tracks:
                if t.name.startswith("WGL_"):
                    continue
                s = next((s for s in t.strips), None)
                if s and s.action:
                    candidate_action, candidate_track = s.action, t
                    break
            if not candidate_action:
                cur = obj.animation_data.action
                if cur and not cur.name.startswith("Act_Sim_"):
                    candidate_action = cur

        if candidate_action and candidate_action != action:
            action = candidate_action
            layer.action_name = action.name
            existing_base = next((t for t in obj.animation_data.nla_tracks if t.name == "WGL_Base"), None)
            # Bug fix: use name comparison here too instead of "is not"
            # (identity comparison) - Blender can hand back a different
            # Python wrapper instance for the same underlying track on
            # different accesses.
            if candidate_track and (not existing_base or candidate_track.name != existing_base.name):
                # The candidate lived on a separate track (e.g. one named
                # "Sections") - absorb that track itself into "WGL_Base"
                # instead of creating a duplicate.
                if existing_base:
                    obj.animation_data.nla_tracks.remove(existing_base)
                # Bug fix / safety: only touch blend_type/influence on the
                # absorbed track if it has exactly one strip (a simple
                # structure this addon can manage). If it has multiple
                # strips (the user's own composed animated-influence
                # blending, for example), never touch them - just rename
                # the track to bookmark it as "WGL_Base". In that case the
                # Layer Weight slider only works via track mute (see
                # sync_layers below).
                candidate_track.name = "WGL_Base"
                track = candidate_track
                if len(track.strips) == 1:
                    for s in track.strips:
                        s.blend_type = 'REPLACE'
                        s.extrapolation = 'HOLD'
                        set_strip_influence(s, layer.influence)
            elif existing_base:
                track = existing_base
                if len(track.strips) == 1:
                    for s in track.strips:
                        s.action = action
                        set_strip_influence(s, layer.influence)
            else:
                track = None
            # Removed entirely: code used to live here that cleared the
            # active action - even scoped to "only once", it kept
            # re-triggering every time the user selected the Base
            # animation action in the Dope Sheet and pressed play,
            # causing the selection to disappear. The active action is
            # never touched by this function anymore, under any
            # circumstance.

        if not action:
            if layer.action_name:
                # Bug fix: layer.action_name pointed at a real action
                # that no longer exists (the user deleted it - e.g.
                # cleaning up "old" actions after baking). Silently
                # spawning a fresh blank replacement here used to hide
                # the deletion entirely (the user's "there's no Base
                # action after I delete the old ones and play" report
                # was this: a brand-new empty "Base_Action" kept getting
                # created behind their back, so Base looked present in
                # the layer list but contributed nothing - a combined
                # bake taken in that state naturally only reflected the
                # Sim layer, since Base had nothing left to combine).
                # Mute the layer instead so the gap is visible, and
                # leave its track/strip alone - an actionless strip just
                # contributes nothing, which is harmless.
                if not layer.mute:
                    layer.mute = True
                    print(f"Wiggle2: '{layer.name}' layer's action '{layer.action_name}' no longer exists - layer muted.")
                return None, track
            # A brand-new blank action doesn't need to be shown as the
            # active action (it's about to be linked into WGL_Base right
            # below anyway) - making it the active action would double-
            # apply on top of the NLA stack, so don't.
            action = bpy.data.actions.new("Base_Action")
            layer.action_name = action.name
            track = None

        if not track:
            track = next((t for t in obj.animation_data.nla_tracks if t.name == "WGL_Base"), None)
            if not track:
                track = obj.animation_data.nla_tracks.new()
                track.name = "WGL_Base"
            # Bug fix: a track named "WGL_Base" can already exist with a
            # strip inside it that references a stale/different action
            # (action was resolved but _find_track_for_action found no
            # match for it). Blindly calling track.strips.new() on a
            # track that already occupies that frame range throws
            # "Unable to add strip (the track does not have any space to
            # accommodate this new strip)". If the track already has
            # exactly one strip, repoint it instead of creating a new one.
            if track.strips:
                if len(track.strips) == 1:
                    strip = track.strips[0]
                    strip.action = action
                    strip.blend_type = 'REPLACE'
                    strip.extrapolation = 'HOLD'
                    set_strip_influence(strip, layer.influence)
                # if there are multiple strips, leave them alone - this is
                # not a track this addon created on its own.
            else:
                start_fr = bpy.context.scene.frame_start if bpy.context.scene else 1
                strip = track.strips.new(action.name, int(start_fr), action)
                strip.blend_type = 'REPLACE'
                strip.extrapolation = 'HOLD'
                set_strip_influence(strip, layer.influence)

        track.name = "WGL_Base"
    else:
        # Sim layer - if it has no action yet, create a blank one and
        # link it (baking will fill it with real keyframes later).
        if not action:
            if layer.action_name:
                # Same fix as the Base branch above: don't silently
                # recreate a deleted action. Recreating here also had a
                # second bug - layer.name had already been auto-renamed
                # to match the deleted action's name (see the bottom of
                # this function), so the "new" action ended up named
                # f"Act_Sim_{layer.name}" = a doubled prefix like
                # "Act_Sim_Act_Sim_Sim Layer 1", and since nothing
                # referenced it yet, a brand-new duplicate track got
                # created too instead of reusing the orphaned old one.
                if not layer.mute:
                    layer.mute = True
                    print(f"Wiggle2: '{layer.name}' layer's action '{layer.action_name}' no longer exists - layer muted.")
                return None, track
            action = bpy.data.actions.new(name=f"Act_Sim_{layer.name}")
            layer.action_name = action.name
            track = None

        if not track:
            # Bug fix: this used to look up the track by
            # f"WGL_Trk_{layer.name}", a name that can change (the layer
            # name auto-renames to match the action name, right below).
            # So after a rename, the next sync couldn't find the existing
            # track and created a second one (e.g. both
            # "WGL_Trk_Sim_Layer_1" and
            # "WGL_Trk_Act_Sim_Sim_Layer_1" ended up existing), and the
            # orphaned old track stuck around interfering visually.
            # Instead of the name, use the `track` already resolved above
            # via _find_track_for_action (keyed on the action itself),
            # and only create a new one if truly none exists. Name the
            # track after the action (stable) rather than the layer.
            track_name = f"WGL_Trk_{action.name}".replace(" ", "_")
            track = obj.animation_data.nla_tracks.new()
            track.name = track_name
            strip = next((s for s in track.strips), None)
            if not strip:
                start_fr = bpy.context.scene.frame_start if bpy.context.scene else 1
                strip = track.strips.new(action.name, int(start_fr), action)
                # Bug fix: COMBINE adds this strip's "delta" on top of the
                # stack below (Base) scaled by influence - if the delta is
                # large, a small bump in influence makes the result jump,
                # not a smooth cross-fade. REPLACE linearly blends the
                # stack-below result against this strip by influence, so
                # Weight 0..1 smoothly crosses Base<->Sim.
                strip.blend_type = 'REPLACE'
                strip.extrapolation = 'HOLD'
                set_strip_influence(strip, layer.influence)

    if not layer.name or layer.name.startswith("Layer") or layer.name.startswith("Sim Layer"):
        layer.name = action.name
    return action, track


def sync_layers(obj):
    """Ensure every layer is linked to a real action/track, and apply
    Layer Weight to NLA strip influence and Sim Mix to live physics
    strength, independently of each other."""
    if not obj or not hasattr(obj, "wiggle_layers"):
        return
    layers = obj.wiggle_layers
    if not layers:
        return

    # Reverted: code used to live here that automatically dropped
    # action_influence to 0 whenever the active action
    # (obj.animation_data.action) overlapped with a layer's action.
    # (Context: Blender applies the active action on top of the NLA
    # stack even while NLA tracks exist - if the user leaves a layer's
    # action selected as the active action in the Dope Sheet/Action
    # Editor, it can look double-applied. A known limitation. But every
    # attempt to auto-fix this produced a new side effect (selection
    # disappearing during playback, keyframe insertion rejected during
    # baking), so the simpler, more predictable option was chosen:
    # leave it alone.)

    # If no layer in the list has type BASE (e.g. legacy data), promote
    # the first one.
    if not any(l.type == 'BASE' for l in layers):
        layers[0].type = 'BASE'

    if not obj.animation_data:
        obj.animation_data_create()

    # Guard: while the user has a layer's strip open in NLA Tweak Mode
    # (see select_layer_for_editing() below - this is what shows a
    # layer's action in the Dope Sheet), none of the structural NLA work
    # below may run. Deleting/recreating a strip (_sync_strip_range) or
    # touching track state while Blender's tweak-mode bookkeeping is
    # tracking that exact strip would corrupt it. Skip straight to the
    # live physics strength update, which only touches bone properties.
    if obj.animation_data.use_tweak_mode:
        physics_strength = 0.0
        for layer in layers:
            if layer.type == 'SIM' and not layer.mute:
                physics_strength += layer.influence * layer.sim_mix
        physics_strength = max(0.0, min(1.0, physics_strength))
        for pb in obj.pose.bones:
            if getattr(pb, "wiggle_tail", False) or getattr(pb, "wiggle_head", False):
                try:
                    pb.wiggle_influence = physics_strength
                except Exception:
                    pass
        return physics_strength

    # Verified by measurement: Blender's NLA track stack evaluates such
    # that the track created LATER (= higher index in the nla_tracks
    # collection) wins - confirmed directly by putting different values
    # on two REPLACE tracks and reading back the evaluated result. So
    # Base should be created first at the lower index (foundation), and
    # Sim layers created after it at higher indices (cross-fade on top) -
    # this is the original ordering, kept as-is.
    # Bug fix: the auto-link/dedupe/reorder logic below varies in
    # structure from file to file and can throw an unexpected exception,
    # which would abort this function partway through and skip the most
    # important final step (forcing blend_type/updating influence to
    # actually reflect Layer Weight) - if that happens, moving the
    # Weight slider does nothing visible at all. Guard each step
    # individually so one failing step doesn't block the rest, and the
    # final critical step always runs.
    base_layer_first = next((l for l in layers if l.type == 'BASE'), None)
    try:
        if base_layer_first:
            ensure_layer_linked(obj, base_layer_first, is_first=True)
    except Exception as e:
        print(f"Wiggle2: Base layer link failed({e})")
    for layer in layers:
        if layer.type != 'BASE':
            try:
                ensure_layer_linked(obj, layer, is_first=False)
            except Exception as e:
                print(f"Wiggle2: '{layer.name}' layer link failed({e})")

    # Bug fix: this used to look up tracks by name (based on the layer
    # name, which can change) and create a new one whenever the lookup
    # failed, which could leave duplicate tracks pointing at the same
    # action in the file (e.g. both "WGL_Trk_Sim_Layer_1" and
    # "WGL_Trk_Act_Sim_Sim_Layer_1" existing at once). An unmanaged
    # duplicate track sits in the NLA stack regardless of mute state and
    # makes Layer Weight look like it's blending strangely no matter what
    # you set it to. Make sure exactly one track survives per layer's
    # action.
    try:
        for layer in layers:
            if not layer.action_name:
                continue
            act = bpy.data.actions.get(layer.action_name)
            if not act:
                continue
            dup_tracks = [t for t in obj.animation_data.nla_tracks
                          if any(s.action == act for s in t.strips)]
            if len(dup_tracks) > 1:
                # Bug fix: this used to compare with "t is not keeper"
                # (identity comparison). Blender can hand back a
                # different Python wrapper for the same track on
                # different accesses, so the keeper itself could get
                # misidentified as "a different object" and deleted too
                # (dedup wiping out every track). Use name comparison
                # instead.
                keeper = _find_track_for_action(obj, act)
                for t in dup_tracks:
                    if t.name != keeper.name:
                        obj.animation_data.nla_tracks.remove(t)
    except Exception as e:
        print(f"Wiggle2: duplicate track cleanup failed({e})")

    # Removed entirely: a self-heal routine used to live here that, if
    # the Base track wasn't at collection index 0, wiped and rebuilt
    # every single NLA track on the object from scratch - including
    # tracks this addon does not own. Worse, each strip's snapshot only
    # captured s.influence (a single evaluated value at that instant), so
    # any track driven by animated influence (an F-curve with keyframes)
    # lost that animation entirely and got flattened to one static
    # number after rebuild. This routine ran on every sync_layers() call
    # (i.e. every single frame during playback), which is why re-adding
    # a deleted track kept getting wiped again on the very next frame.
    # Base not being at index 0 doesn't actually affect evaluation order
    # (that's determined purely by which track was created later, not by
    # collection index), so this reordering was an unnecessary
    # optimization that was not worth its blast radius - removed
    # entirely.

    nla_tracks = obj.animation_data.nla_tracks
    action_to_track = {}
    for t in nla_tracks:
        for s in t.strips:
            if s.action:
                action_to_track[s.action.name] = t

    base_layer = next((l for l in layers if l.type == 'BASE'), None)

    def _act_track(layer):
        act = bpy.data.actions.get(layer.action_name) if layer.action_name else None
        return act, action_to_track.get(act.name) if act else None

    # Bug fix: which layer is "selected" in the list is purely a UI
    # editing concern - it must never be used as the basis for muting
    # other layers. This used to only cross-fade the selected layer
    # against Base and unconditionally mute every other Sim layer even
    # if its own Weight was nonzero. Now Base behaves exactly like every
    # Sim layer, turned on/off solely by its own Weight/Mute (Weight 0
    # turns Base off too).
    # Bug fix: this used to also mute the track whenever Weight dropped to
    # ~0 (in addition to setting influence to 0), reasoning that "the
    # track is excluded from evaluation anyway so it's safe". It is NOT
    # safe for one specific thing: Blender does not evaluate a muted
    # track's strips at all, which means strip.influence (the property
    # the NLA panel actually displays) freezes at whatever value it had
    # right before muting - verified directly (fcurve keyframe correctly
    # updates to 0.0, but strip.influence keeps reading the old value,
    # e.g. 0.3, forever, until the track is unmuted again). From the
    # user's side this looked exactly like "Layer Weight doesn't do
    # anything" the moment they dragged it down to 0. Fix: for the normal
    # single-strip case, never auto-mute on Weight alone - influence=0 on
    # an unmuted REPLACE strip already contributes nothing on its own, so
    # muting added no evaluation benefit, only this display bug. Only the
    # explicit per-layer Mute checkbox controls track.mute now.
    # blend_type is also forced to REPLACE every time - a strip left over
    # from the old (Combine) code path would break the smooth cross-fade
    # otherwise.
    try:
        base_act, base_track = _act_track(base_layer) if base_layer else (None, None)
        if base_track:
            # Bug fix / safety: only do range sync + forced blend_type/
            # influence on a track that has exactly one strip (the simple
            # structure this addon built from scratch). If it has
            # multiple strips, it's most likely a track the user composed
            # themselves (e.g. several sections cross-faded via animated
            # influence), and its content must not be overwritten on
            # every sync (including every frame during playback) - in
            # that case influence can't be touched per-strip, so mute is
            # the only way Weight<=0 can still take effect.
            if len(base_track.strips) == 1:
                base_track.mute = base_layer.mute
                _sync_strip_range(base_track, base_act)
                for s in base_track.strips:
                    s.blend_type = 'REPLACE'
                    set_strip_influence(s, base_layer.influence)
            else:
                base_track.mute = base_layer.mute or base_layer.influence <= 0.0001
    except Exception as e:
        print(f"Wiggle2: Base layer Weight apply failed({e})")

    for layer in layers:
        if layer.type != 'SIM':
            continue
        try:
            act, track = _act_track(layer)
            if not track:
                continue
            if len(track.strips) == 1:
                track.mute = layer.mute
                _sync_strip_range(track, act)
                for s in track.strips:
                    s.blend_type = 'REPLACE'
                    set_strip_influence(s, layer.influence)
            else:
                track.mute = layer.mute or layer.influence <= 0.0001
        except Exception as e:
            print(f"Wiggle2: '{layer.name}' Weight apply failed({e})")

    # Live physics strength: sum (Layer Weight x Sim Mix) across every
    # non-muted Sim layer. User settings like wiggle_stiff/damp are never
    # touched.
    physics_strength = 0.0
    for layer in layers:
        if layer.type == 'SIM' and not layer.mute:
            physics_strength += layer.influence * layer.sim_mix
    physics_strength = max(0.0, min(1.0, physics_strength))

    for pb in obj.pose.bones:
        if getattr(pb, "wiggle_tail", False) or getattr(pb, "wiggle_head", False):
            try:
                pb.wiggle_influence = physics_strength
            except Exception:
                pass

    if obj.id_data:
        obj.id_data.update_tag()
    return physics_strength


_baking_in_progress = False


@persistent
def wiggle_frame_change_handler(scene):
    # Bug fix: whenever the bake operator called scene.frame_set() inside
    # its own frame loop, this handler used to fire every frame and run
    # sync_layers(), which touched the active action that had just been
    # set up for baking (the safety code decided "it's already linked to
    # this layer, so it's safe to clear" -> clearing it meant
    # keyframe_insert ran with no action, so Blender auto-created a
    # throwaway action -> the Base auto-detect logic then mistakenly
    # adopted that throwaway action as the real Base). This handler is
    # skipped entirely while a bake is in progress.
    if _baking_in_progress:
        return
    obj = bpy.context.object
    if obj and obj.type == 'ARMATURE' and hasattr(obj, "wiggle_layers") and obj.wiggle_layers:
        with NoAutoKey(scene):
            sync_layers(obj)


def update_layer_params(self, context):
    """Apply immediately when the Layer Weight / Sim Mix / Mute sliders
    move."""
    obj = context.object
    if obj:
        sync_layers(obj)
        # Bug fix: set_strip_influence() changes an NLA strip's influence
        # F-curve keyframe value, but without a call forcing the
        # viewport to re-evaluate immediately, nothing visibly updates
        # until the frame actually changes (moving the slider does
        # nothing on screen).
        try:
            context.view_layer.update()
        except Exception:
            pass
        try:
            from . import wiggle_2
            # The actual physics computation only runs on the
            # frame_change_post handler (i.e. only when the frame
            # changes), so moving just the slider without touching the
            # timeline wouldn't update the live physics preview. Without
            # recomputing physics, re-blend the last frame's already-
            # cached animation/simulation pose immediately using the new
            # wiggle_influence (no reset side effects).
            wiggle_2.build_list()
            wiggle_2.refresh_influence_blend(obj)
        except Exception:
            pass
    if context.screen:
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _exit_tweak_mode(obj):
    """Cleanly leave NLA Tweak Mode if it's currently active, so a
    different layer's strip can be entered (or normal structural sync can
    resume - sync_layers() refuses to touch NLA structure while tweak
    mode is on, see the guard near the top of that function)."""
    if obj.animation_data and obj.animation_data.use_tweak_mode:
        try:
            obj.animation_data.use_tweak_mode = False
        except Exception:
            pass


def select_layer_for_editing(obj, layer):
    """Tried and reverted: automatically entering NLA Tweak Mode on the
    selected layer's strip, so its action would show up in the Dope
    Sheet/Action Editor without the double-application problem a plain
    obj.animation_data.action assignment causes. This worked correctly
    when there was only a single NLA track on the object, but with two or
    more tracks, directly toggling AnimData.use_tweak_mode from Python
    does not reliably respect which strip was select=True or which strip
    resolved via lookup - verified directly (muting the other track,
    deselecting everything but the target strip, forcing a depsgraph
    update in between) and it kept entering tweak mode on the wrong
    strip, or on none at all (action ends up None). Blender's actual
    "double-click a strip in the NLA editor" operator
    (bpy.ops.nla.tweakmode_enter) uses editor-context state
    (context.active_nla_track / the NLA editor's own selection) that
    isn't reachable through the data API alone, and the operator's own
    poll() refuses to run without a real NLA_EDITOR area, which the
    Properties/3D-viewport panel this addon lives in cannot fake safely.
    Left as a no-op rather than risk silently showing the wrong action.
    No safe way was found to do this - if you need to inspect or edit a
    layer's action, double-click its strip directly in the NLA editor
    (Blender's own Tweak Mode) or select it via the Action Editor's
    action dropdown."""
    pass


def update_layer_selection(self, context):
    """Selecting a layer in the Sim Mix Layers list only re-syncs; it
    does not touch the active action. See select_layer_for_editing() for
    why an automatic "show this layer's action in the Dope Sheet"
    feature was attempted and reverted."""
    obj = context.object
    if not obj or not hasattr(obj, "wiggle_layers"):
        return
    idx = getattr(obj, "wiggle_layer_index", -1)
    if 0 <= idx < len(obj.wiggle_layers):
        sync_layers(obj)


class WiggleSimLayer(bpy.types.PropertyGroup):
    name:        bpy.props.StringProperty(name="Layer Name", default="New Layer")
    action_name: bpy.props.StringProperty(name="Action Data")
    type:        bpy.props.EnumProperty(
        items=[('BASE', "Base (Anim)", ""), ('SIM', "Simulation", "")],
        name="Type", default='SIM'
    )
    influence: bpy.props.FloatProperty(name="Layer Weight", default=1.0, min=0.0, max=1.0, update=update_layer_params)
    sim_mix:   bpy.props.FloatProperty(name="Sim Mix",      default=1.0, min=0.0, max=1.0, update=update_layer_params)
    mute:      bpy.props.BoolProperty(name="Mute",          default=False, update=update_layer_params)


class WIGGLE_OT_ExitTweakMode(bpy.types.Operator):
    """While an NLA strip is in Tweak Mode (e.g. from double-clicking a
    strip in the NLA editor), sync_layers() deliberately refuses to touch
    NLA structure to avoid corrupting Blender's tweak-mode bookkeeping -
    but that also means Layer Weight stops visibly doing anything until
    tweak mode is exited, with no indication why. This gives the user a
    one-click way out."""
    bl_idname  = "wiggle.exit_tweak_mode"
    bl_label   = "Exit NLA Tweak Mode"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.object
        if not obj or not obj.animation_data:
            return {'CANCELLED'}
        _exit_tweak_mode(obj)
        sync_layers(obj)
        if context.screen:
            for area in context.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
        self.report({'INFO'}, "Exited NLA Tweak Mode - Layer Weight is active again.")
        return {'FINISHED'}


class WIGGLE_UL_SimMixLayers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        row = layout.row(align=True)
        row.prop(item, "mute", text="",
                 icon='CHECKBOX_DEHLT' if item.mute else 'CHECKBOX_HLT', emboss=False)
        row.label(text="", icon='ANIM' if item.type == 'BASE' else 'PHYSICS')
        row.prop(item, "name", text="", emboss=False)
        if item.type == 'SIM':
            pct = 0 if item.mute else int(item.influence * item.sim_mix * 100)
            row.label(text=f"{pct}%")


class WIGGLE_OT_LayerAction(bpy.types.Operator):
    bl_idname  = "wiggle.layer_action"
    bl_label   = "Layer Action"
    bl_options = {'REGISTER', 'UNDO'}
    action: bpy.props.EnumProperty(
        items=[('ADD', 'Add', ''), ('REMOVE', 'Remove', ''), ('UP', 'Up', ''), ('DOWN', 'Down', '')]
    )

    def execute(self, context):
        obj = context.object
        if not obj or obj.type != 'ARMATURE':
            return {'CANCELLED'}
        if not obj.animation_data:
            obj.animation_data_create()
        # Safety: structural NLA changes (adding/removing tracks) while a
        # strip is in Tweak Mode can corrupt Blender's tweak-mode
        # bookkeeping. Always leave tweak mode before making any change
        # here.
        _exit_tweak_mode(obj)
        layers = obj.wiggle_layers
        idx    = obj.wiggle_layer_index

        if self.action == 'ADD':
            is_first = (len(layers) == 0)
            l = layers.add()
            if is_first:
                l.type = 'BASE'
                l.name = "Base (Anim)"
            else:
                l.type = 'SIM'
                l.name = f"Sim Layer {len(layers) - 1}"
                # The first Sim layer you add starts at Weight 100%
                # (default 1.0).
                l.influence = 1.0
            ensure_layer_linked(obj, l, is_first=is_first)
            obj.wiggle_layer_index = len(layers) - 1
        elif self.action == 'REMOVE' and 0 <= idx < len(layers):
            layer = layers[idx]
            if layer.type == 'BASE':
                self.report({'WARNING'}, "Can't remove the Base layer.")
                return {'CANCELLED'}
            act = bpy.data.actions.get(layer.action_name) if layer.action_name else None
            track = _find_track_for_action(obj, act) if act else None
            if track:
                obj.animation_data.nla_tracks.remove(track)
            layers.remove(idx)
            obj.wiggle_layer_index = max(0, idx - 1)
        elif self.action == 'UP' and idx > 0:
            layers.move(idx, idx - 1)
            obj.wiggle_layer_index -= 1
        elif self.action == 'DOWN' and idx < len(layers) - 1:
            layers.move(idx, idx + 1)
            obj.wiggle_layer_index += 1

        sync_layers(obj)
        return {'FINISHED'}


# ============================================================
# Bake progress (shared by the modal bake operators)
# ------------------------------------------------------------
# A plain for-loop over scene.frame_set() blocks Blender's event loop,
# so nothing can redraw until the bake is over (it looks frozen). The
# bake operators below run as modal operators instead: each timer tick
# processes as many frames as fit in a short time budget, then hands
# control back so Blender can redraw the viewport, the status bar and
# the progress bar in the Bake panel. Esc cancels.
# ============================================================
import time

_bake_modal_running = False


def bake_running():
    return _bake_modal_running


def _redraw_ui(context):
    wm = context.window_manager
    for win in wm.windows:
        scr = win.screen
        if not scr:
            continue
        for area in scr.areas:
            if area.type in {'VIEW_3D', 'PROPERTIES', 'DOPESHEET_EDITOR',
                             'GRAPH_EDITOR', 'NLA_EDITOR', 'TIMELINE'}:
                area.tag_redraw()


def _set_status(context, text):
    ws = getattr(context, "workspace", None)
    if ws:
        try:
            ws.status_text_set(text)
        except Exception:
            pass


def bake_progress_begin(context, total, label):
    global _bake_modal_running
    _bake_modal_running = True
    wm = context.window_manager
    wm.wiggle_bake_progress = 0.0
    wm.wiggle_bake_status = label
    try:
        wm.progress_begin(0, max(1, total))
    except Exception:
        pass
    _set_status(context, f"{label}  (Esc to cancel)")
    _redraw_ui(context)


def bake_progress_update(context, done, total, label, can_cancel=True):
    wm = context.window_manager
    frac = (done / total) if total else 1.0
    wm.wiggle_bake_progress = min(max(frac, 0.0), 1.0)
    wm.wiggle_bake_status = label
    try:
        wm.progress_update(done)
    except Exception:
        pass
    hint = "  (Esc to cancel)" if can_cancel else ""
    _set_status(context, f"{label}  {int(frac * 100)}%{hint}")
    _redraw_ui(context)


def bake_progress_end(context):
    global _bake_modal_running
    _bake_modal_running = False
    wm = context.window_manager
    wm.wiggle_bake_progress = 0.0
    wm.wiggle_bake_status = ""
    try:
        wm.progress_end()
    except Exception:
        pass
    _set_status(context, None)
    _redraw_ui(context)


class ModalBakeMixin:
    """Runs a bake as a list of small steps. Subclasses implement:
      _bake_prepare(context) -> bool   set up and fill self._steps
      _bake_step(context, step)        do one step
      _bake_label(step) -> str         text for the status bar / panel
      _bake_can_cancel(step) -> bool   False once cancelling is unsafe
      _bake_finish(context) -> set     wrap up, return {'FINISHED'}
      _bake_abort(context)             undo / restore after cancel or error
    Clicking the button runs invoke() (modal, with progress). Calling the
    operator from a script runs execute() (the same steps, synchronously)."""

    _timer = None
    _index = 0
    _steps = ()
    _TIME_BUDGET = 0.05  # seconds of work per tick before redrawing

    @classmethod
    def poll(cls, context):
        return not _bake_modal_running

    def execute(self, context):
        if not self._bake_prepare(context):
            return {'CANCELLED'}
        try:
            for step in self._steps:
                self._bake_step(context, step)
        except Exception:
            self._bake_abort(context)
            raise
        return self._bake_finish(context)

    def invoke(self, context, event):
        if not self._bake_prepare(context):
            return {'CANCELLED'}
        self._index = 0
        if not self._steps:
            return self._bake_finish(context)
        wm = context.window_manager
        bake_progress_begin(context, len(self._steps), self._bake_label(self._steps[0]))
        self._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _stop_modal(self, context):
        if self._timer is not None:
            try:
                context.window_manager.event_timer_remove(self._timer)
            except Exception:
                pass
            self._timer = None
        bake_progress_end(context)

    def modal(self, context, event):
        total = len(self._steps)
        cur = self._steps[min(self._index, total - 1)]

        if event.type == 'ESC' and event.value == 'PRESS':
            if self._bake_can_cancel(cur):
                self._stop_modal(context)
                try:
                    self._bake_abort(context)
                finally:
                    self.report({'WARNING'}, "Wiggle bake cancelled")
                return {'CANCELLED'}
            return {'RUNNING_MODAL'}

        if event.type != 'TIMER':
            # Swallow other input so clicks/scrubbing can't interfere
            # with the bake while it runs.
            return {'RUNNING_MODAL'}

        deadline = time.perf_counter() + self._TIME_BUDGET
        try:
            while self._index < total:
                self._bake_step(context, self._steps[self._index])
                self._index += 1
                if time.perf_counter() >= deadline:
                    break
        except Exception as e:
            self._stop_modal(context)
            try:
                self._bake_abort(context)
            finally:
                self.report({'ERROR'}, f"Wiggle bake failed: {e}")
            return {'CANCELLED'}

        if self._index >= total:
            self._stop_modal(context)
            return self._bake_finish(context)

        nxt = self._steps[self._index]
        bake_progress_update(context, self._index, total,
                             self._bake_label(nxt), self._bake_can_cancel(nxt))
        return {'RUNNING_MODAL'}


class WIGGLE_OT_BakeCombined(ModalBakeMixin, bpy.types.Operator):
    """Bakes the live blended result of the active Sim layer into the
    action already linked to that layer, or directly overwrites the active action.
    Honors Preroll/Overwrite/NLA settings. Shows progress; press Esc to cancel"""
    bl_idname   = "wiggle.bake_combined"
    bl_label    = "Bake Combined"
    bl_options  = {'REGISTER', 'UNDO'}

    def _bake_prepare(self, context):
        obj   = context.object
        scene = context.scene
        if not obj or obj.type != 'ARMATURE':
            return False

        w_pre = scene.wiggle
        self._smart = bool(getattr(w_pre, "bake_smart", False))
        self._direct_overwrite = bool(getattr(w_pre, "bake_overwrite", False))
        
        if self._smart:
            names = [pb.name for pb in obj.pose.bones
                     if (getattr(pb, "wiggle_tail", False)
                         or (getattr(pb, "wiggle_head", False) and not pb.bone.use_connect))
                     and not getattr(pb, "wiggle_mute", False)]
            if not names:
                self.report({'WARNING'}, "Smart Bake: no active wiggle bones to bake.")
                return False
        else:
            names = [pb.name for pb in obj.pose.bones]
            if not names:
                self.report({'WARNING'}, "No bones to bake.")
                return False
        self._bake_bone_names = names

        self._obj_name    = obj.name
        self._start_frame = scene.frame_start
        self._end_frame   = scene.frame_end
        self._orig_frame  = scene.frame_current
        w = scene.wiggle

        if obj.mode != 'POSE':
            bpy.ops.object.mode_set(mode='POSE')
        if not obj.animation_data:
            obj.animation_data_create()
        _exit_tweak_mode(obj)

        layers = obj.wiggle_layers
        self._orig_layer_index = getattr(obj, "wiggle_layer_index", 0)
        idx = self._orig_layer_index
        selected_layer = layers[idx] if 0 <= idx < len(layers) else None

        self._created_layer = False
        self._created_prev_track_name = None

        if self._direct_overwrite:
            target_action = obj.animation_data.action
            if not target_action:
                target_action = bpy.data.actions.new(name="WiggleAction")
            self._target_action = target_action
        else:
            active_layer = None
            if selected_layer and selected_layer.type == 'SIM':
                active_layer = selected_layer
                target_action = bpy.data.actions.get(active_layer.action_name) if active_layer.action_name else None
                if not target_action:
                    target_action = bpy.data.actions.new(name=f"Act_Sim_{active_layer.name}")
                ensure_layer_linked(obj, active_layer, is_first=False)

            if active_layer is None:
                existing_names = {l.name for l in layers}
                n = 1
                new_name = "Bake"
                while new_name in existing_names or bpy.data.actions.get(f"Act_Sim_{new_name}"):
                    n += 1
                    new_name = f"Bake {n}"
                active_layer = layers.add()
                active_layer.type = 'SIM'
                active_layer.name = new_name
                target_action = bpy.data.actions.new(name=f"Act_Sim_{active_layer.name}")
                active_layer.action_name = target_action.name
                ensure_layer_linked(obj, active_layer, is_first=False)
                self._created_layer = True

            self._layer_name  = active_layer.name
            self._action_name = target_action.name
            obj.wiggle_layer_index = list(layers).index(active_layer)

            target_track = _find_track_for_action(obj, target_action)
            self._target_track_prev_mute = target_track.mute if target_track else None
            if target_track:
                target_track.mute = True

        if getattr(w, "bake_nla", False) and obj.animation_data.action and not self._direct_overwrite:
            old_action = obj.animation_data.action
            track = obj.animation_data.nla_tracks.new()
            track.name = "WGL_PrevAction"
            track.strips.new(old_action.name, int(old_action.frame_range[0]), old_action)
            self._created_prev_track_name = track.name

        global _baking_in_progress
        _baking_in_progress = True
        self._captured = []
        self._write_started = False

        steps = []
        self._has_preroll = getattr(w, "preroll", 0) > 0
        try:
            from . import wiggle_2 as _w2
            _w2._BAKE_NO_CACHE = self._has_preroll
        except Exception:
            pass
        if self._has_preroll:
            preroll_start = self._start_frame - max(1, w.preroll)
            steps += [('PREROLL', f) for f in range(preroll_start, self._start_frame)]
        steps += [('CAPTURE', f) for f in range(self._start_frame, self._end_frame + 1)]
        steps.append(('BEGIN_WRITE', None))
        steps += [('WRITE', i) for i in range(self._end_frame - self._start_frame + 1)]
        self._steps = steps
        return True

    def _obj(self):
        return bpy.data.objects.get(self._obj_name)

    def _layer(self, obj):
        for l in obj.wiggle_layers:
            if l.name == self._layer_name:
                return l
        return None

    def _bake_label(self, step):
        kind, val = step
        if kind == 'PREROLL':
            return f"Wiggle bake: preroll frame {val}"
        if kind == 'CAPTURE':
            return f"Wiggle bake: simulating frame {val} / {self._end_frame}"
        if kind == 'BEGIN_WRITE':
            return "Wiggle bake: preparing keyframes"
        return (f"Wiggle bake: writing keyframes "
                f"{self._start_frame + val} / {self._end_frame}")

    def _bake_can_cancel(self, step):
        return step[0] in {'PREROLL', 'CAPTURE'}

    def _bake_step(self, context, step):
        kind, val = step
        scene = context.scene
        obj = self._obj()
        w = scene.wiggle

        if kind == 'PREROLL':
            w.is_preroll = True
            scene.frame_set(val)
            context.view_layer.update()

        elif kind == 'CAPTURE':
            # The start frame must continue from the preroll (not reset),
            # so the preroll flag stays on for exactly that one frame.
            w.is_preroll = bool(self._has_preroll and val == self._start_frame)
            scene.frame_set(val)
            context.view_layer.update()
            w.is_preroll = False
            frame_data = {}
            for name in self._bake_bone_names:
                pb = obj.pose.bones.get(name)
                if pb is None:
                    continue
                if pb.rotation_mode == 'QUATERNION':
                    rot = pb.rotation_quaternion.copy()
                elif pb.rotation_mode == 'AXIS_ANGLE':
                    rot = pb.rotation_axis_angle[:]
                else:
                    rot = pb.rotation_euler.copy()
                frame_data[pb.name] = (pb.location.copy(), pb.rotation_mode, rot, pb.scale.copy())
            self._captured.append((val, frame_data))

        elif kind == 'BEGIN_WRITE':
            self._write_started = True
            w.is_preroll = False

            if self._direct_overwrite:
                target_action = self._target_action
                _clear_action_keyframes(target_action, self._bake_bone_names)
                obj.animation_data.action = target_action
            else:
                target_action = bpy.data.actions.get(self._action_name)
                _clear_action_keyframes(target_action, self._bake_bone_names)
                layer = self._layer(obj)
                if layer:
                    layer.influence = 1.0
                    layer.sim_mix = 0.0

                # The influence update re-syncs layers, so mute again here.
                target_track = _find_track_for_action(obj, target_action)
                if target_track:
                    target_track.mute = True

                # Write straight into the action; no re-evaluation happens
                # during writing, so the active-action overlay issue can't occur.
                obj.animation_data.action = target_action
                if hasattr(obj.animation_data, "action_influence"):
                    obj.animation_data.action_influence = 1.0
                if hasattr(obj.animation_data, "action_blend_type"):
                    obj.animation_data.action_blend_type = 'REPLACE'

        elif kind == 'WRITE':
            f, frame_data = self._captured[val]
            for name in self._bake_bone_names:
                pb = obj.pose.bones.get(name)
                if pb is None or name not in frame_data:
                    continue
                loc, rot_mode, rot, scale = frame_data[pb.name]
                pb.location = loc
                pb.keyframe_insert(data_path="location", frame=f, group=pb.name)
                if rot_mode == 'QUATERNION':
                    pb.rotation_quaternion = rot
                    pb.keyframe_insert(data_path="rotation_quaternion", frame=f, group=pb.name)
                elif rot_mode == 'AXIS_ANGLE':
                    pb.rotation_axis_angle = rot
                    pb.keyframe_insert(data_path="rotation_axis_angle", frame=f, group=pb.name)
                else:
                    pb.rotation_euler = rot
                    pb.keyframe_insert(data_path="rotation_euler", frame=f, group=pb.name)
                pb.scale = scale
                pb.keyframe_insert(data_path="scale", frame=f, group=pb.name)

    def _restore_track_mute(self, obj):
        target_action = bpy.data.actions.get(self._action_name)
        target_track = _find_track_for_action(obj, target_action) if target_action else None
        if target_track:
            prev = self._target_track_prev_mute
            target_track.mute = prev if prev is not None else False

    def _clear_no_cache(self):
        try:
            from . import wiggle_2 as _w2
            _w2._BAKE_NO_CACHE = False
        except Exception:
            pass

    def _bake_finish(self, context):
        global _baking_in_progress
        _baking_in_progress = False
        self._clear_no_cache()
        scene = context.scene
        obj = self._obj()
        scene.wiggle.is_preroll = False
        self._captured = []
        if obj:
            if not self._direct_overwrite:
                obj.animation_data.action = None
                self._restore_track_mute(obj)
            obj.wiggle_freeze = True
        scene.frame_set(self._orig_frame)
        if obj and not self._direct_overwrite:
            sync_layers(obj)
        what = (f"{len(self._bake_bone_names)} physics bones (Smart Bake)"
                if self._smart else "all bones")
        action_name = self._target_action.name if self._direct_overwrite else self._action_name
        self.report({'INFO'}, f"Bake Complete into '{action_name}' ({what}): "
                              f"{self._start_frame}~{self._end_frame}")
        return {'FINISHED'}

    def _bake_abort(self, context):
        global _baking_in_progress
        _baking_in_progress = False
        self._clear_no_cache()
        scene = context.scene
        scene.wiggle.is_preroll = False
        self._captured = []
        obj = self._obj()
        if obj and obj.animation_data:
            if self._write_started and not self._direct_overwrite:
                obj.animation_data.action = None
                self._restore_track_mute(obj)
            elif not self._direct_overwrite:
                ad = obj.animation_data
                if self._created_prev_track_name:
                    tr = ad.nla_tracks.get(self._created_prev_track_name)
                    if tr:
                        ad.nla_tracks.remove(tr)
                if self._created_layer:
                    act = bpy.data.actions.get(self._action_name)
                    tr = _find_track_for_action(obj, act) if act else None
                    if tr:
                        ad.nla_tracks.remove(tr)
                    layers = obj.wiggle_layers
                    for i, l in enumerate(layers):
                        if l.name == self._layer_name:
                            layers.remove(i)
                            break
                    if act and act.users == 0:
                        bpy.data.actions.remove(act)
                else:
                    self._restore_track_mute(obj)
                n = len(obj.wiggle_layers)
                if n:
                    obj.wiggle_layer_index = min(max(self._orig_layer_index, 0), n - 1)
        scene.frame_set(self._orig_frame)
        if obj and not self._direct_overwrite:
            sync_layers(obj)


classes = (
    WiggleSimLayer,
    WIGGLE_UL_SimMixLayers,
    WIGGLE_OT_LayerAction,
    WIGGLE_OT_BakeCombined,
    WIGGLE_OT_ExitTweakMode,
)


def register():
    # Bug fix: register_class throws a ValueError when called again on an
    # already-registered class (Reload Scripts, re-enabling without
    # disabling first, etc.). There used to be no protection here, so
    # register() would stop partway through, silently skipping the rest
    # of the classes/properties/handlers below it.
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass

    bpy.types.Object.wiggle_layers = bpy.props.CollectionProperty(type=WiggleSimLayer)
    bpy.types.Object.wiggle_layer_index = bpy.props.IntProperty(
        name="Idx", default=0, update=update_layer_selection
    )
    # wiggle_influence: default must be 1.0. At 0.0, any bone that just
    # had wiggle turned on gets zero physics until the next sync runs.
    if not hasattr(bpy.types.PoseBone, "wiggle_influence"):
        bpy.types.PoseBone.wiggle_influence = bpy.props.FloatProperty(
            name="Wiggle Influence",
            description="Physics blend ratio (0=Animation only, 1=Full physics)",
            default=1.0, min=0.0, max=1.0
        )

    # Bake progress display (WindowManager props are runtime-only and are
    # never saved into the .blend, so a crash can't leave them stuck).
    bpy.types.WindowManager.wiggle_bake_progress = bpy.props.FloatProperty(
        name="Bake Progress", default=0.0, min=0.0, max=1.0, subtype='FACTOR'
    )
    bpy.types.WindowManager.wiggle_bake_status = bpy.props.StringProperty(
        name="Bake Status", default=""
    )

    if wiggle_frame_change_handler not in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.append(wiggle_frame_change_handler)


def unregister():
    if wiggle_frame_change_handler in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.remove(wiggle_frame_change_handler)

    # Bug fix: while wiggle_layers (a CollectionProperty) still
    # references the WiggleSimLayer PropertyGroup class, unregistering
    # that class first throws and leaves the classes after it in the
    # tuple never unregistered - delete the properties before the
    # classes.
    for attr in ("wiggle_layers", "wiggle_layer_index"):
        if hasattr(bpy.types.Object, attr):
            delattr(bpy.types.Object, attr)
    if hasattr(bpy.types.PoseBone, "wiggle_influence"):
        delattr(bpy.types.PoseBone, "wiggle_influence")
    for attr in ("wiggle_bake_progress", "wiggle_bake_status"):
        if hasattr(bpy.types.WindowManager, attr):
            delattr(bpy.types.WindowManager, attr)

    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass


if __name__ == "__main__":
    register()
