import bpy
from bpy.app.handlers import persistent

# ============================================================
# wiggle_layers.py  ─  Sim Mix Layer 시스템  (v3)
#
# ▣ 핵심 수정
# [Bug-1] sync_combined_result() 의 물리 섹션에서
#          pb.wiggle_stiff = (1.0 - influence) * 80.0  로
#          매 프레임 모든 본의 stiffness를 강제 덮어쓰던 코드 완전 제거.
#          → 사용자가 설정한 stiff 값(프리셋 포함)이 파괴되지 않음.
# [Bug-2] wiggle_influence 기본값 0.0 → 1.0 으로 수정.
#          (0.0 이면 등록 직후 물리가 전혀 작동하지 않음)
# ============================================================

_sync_in_progress = False


def use_animated_influence(strip):
    if strip.use_animated_influence:
        return
    strip.use_animated_influence = True
    if strip.action and hasattr(strip.action, "fcurves"):
        for fcu in strip.action.fcurves:
            if "influence" in fcu.data_path:
                strip.action.fcurves.remove(fcu)
    strip.influence = 1.0


@persistent
def wiggle_frame_change_handler(scene):
    global _sync_in_progress
    if _sync_in_progress:
        return
    try:
        _sync_in_progress = True
        obj = bpy.context.object
        if (obj and obj.type == 'ARMATURE'
                and hasattr(obj, "wiggle_layers")
                and obj.animation_data
                and 0 <= getattr(obj, "wiggle_layer_index", -1) < len(obj.wiggle_layers)):
            sync_combined_result(obj)
    except Exception:
        pass
    finally:
        _sync_in_progress = False


def sync_combined_result(obj):
    """Layer Weight(NLA 블렌드) + Sim Mix(Physics influence) 적용."""
    if not obj or not obj.animation_data:
        return

    layers      = obj.wiggle_layers
    idx         = obj.wiggle_layer_index
    active_layer = layers[idx] if 0 <= idx < len(layers) else None
    nla_tracks  = getattr(obj.animation_data, "nla_tracks", [])

    # ─── NLA 기반 기존 액션 탐색 ────────────────────────────
    existing_action = None
    for track in nla_tracks:
        if track.name != "WGL_Base" and not track.name.startswith("WGL_Trk_"):
            s = next((s for s in track.strips), None)
            if s and s.action:
                existing_action = s.action
                break

    base_track = next((t for t in nla_tracks if t.name.startswith("WGL_Base")), None)
    has_strips = any(True for _ in base_track.strips) if base_track else False
    target_action = None

    if not base_track or not has_strips:
        if existing_action:
            target_action = existing_action
        elif obj.animation_data.action and not obj.animation_data.action.name.startswith("Act_Sim_"):
            target_action = obj.animation_data.action
        else:
            base_layer = next((l for l in layers if l.type == 'BASE'), None)
            if base_layer and getattr(base_layer, "action_name", None):
                target_action = bpy.data.actions.get(base_layer.action_name)
        if not target_action and base_track:
            s = next((s for s in base_track.strips), None)
            if s and s.action:
                target_action = s.action

    if target_action:
        if not base_track:
            base_track = obj.animation_data.nla_tracks.new()
            base_track.name = "WGL_Base"
        else:
            base_track.name = "WGL_Base"
        base_track.lock = False
        base_track.mute = False

        if not has_strips:
            start_fr = bpy.context.scene.frame_start if bpy.context.scene else 1
            strip = base_track.strips.new(target_action.name, int(start_fr), target_action)
            strip.blend_type   = 'REPLACE'
            strip.extrapolation = 'HOLD'
            strip.influence    = 1.0
            use_animated_influence(strip)

        if existing_action:
            obj.animation_data.action = existing_action
        else:
            if obj.animation_data.action == target_action:
                obj.animation_data.action = None

        for extra in [t for t in nla_tracks if t.name.startswith("WGL_Base")][1:]:
            obj.animation_data.nla_tracks.remove(extra)

        if hasattr(obj.animation_data, "use_tweak_mode") and obj.animation_data.use_tweak_mode:
            obj.animation_data.use_tweak_mode = False

        if existing_action and existing_action.frame_range and bpy.context.scene:
            s, e = existing_action.frame_range
            bpy.context.scene.frame_start = int(s)
            bpy.context.scene.frame_end   = int(e)

    # ─── NLA 트랙 매핑 캐시 ─────────────────────────────────
    nla_tracks = getattr(obj.animation_data, "nla_tracks", [])
    action_to_tracks = {}
    for t in nla_tracks:
        for s in t.strips:
            if s.action:
                action_to_tracks.setdefault(s.action.name, []).append(t)

    # ─── 레이어 기본 상태 ────────────────────────────────────
    for layer in layers:
        if not layer.action_name:
            continue
        act = bpy.data.actions.get(layer.action_name)
        if not act:
            continue
        for t in action_to_tracks.get(act.name, []):
            t.mute = layer.mute
            for s in t.strips:
                s.blend_type   = 'REPLACE' if layer.type == 'BASE' else 'COMBINE'
                s.extrapolation = 'HOLD'

    # ─── Layer Weight 크로스페이드 ───────────────────────────
    if active_layer:
        if active_layer.type == 'BASE':
            for layer in layers:
                if layer.type == 'SIM' and layer.action_name:
                    act = bpy.data.actions.get(layer.action_name)
                    if act:
                        for t in action_to_tracks.get(act.name, []):
                            t.mute = True
            if active_layer.action_name:
                act = bpy.data.actions.get(active_layer.action_name)
                if act:
                    for t in action_to_tracks.get(act.name, []):
                        t.mute = False
                        for s in t.strips:
                            s.influence = 1.0
                            use_animated_influence(s)
        else:
            weight         = active_layer.influence
            base_influence = max(0.0, min(1.0, 1.0 - weight))
            base_layer     = next((l for l in layers if l.type == 'BASE'), None)
            if base_layer and base_layer.action_name:
                act = bpy.data.actions.get(base_layer.action_name)
                if act:
                    for t in action_to_tracks.get(act.name, []):
                        t.mute = False
                        for s in t.strips:
                            s.influence = base_influence
                            use_animated_influence(s)
            if active_layer.action_name:
                act = bpy.data.actions.get(active_layer.action_name)
                if act:
                    for t in action_to_tracks.get(act.name, []):
                        if weight <= 0.0001:
                            t.mute = True
                        else:
                            t.mute = False
                            for s in t.strips:
                                s.influence = weight
                                use_animated_influence(s)
            for layer in layers:
                if layer.type == 'SIM' and layer != active_layer and layer.action_name:
                    act = bpy.data.actions.get(layer.action_name)
                    if act:
                        for t in action_to_tracks.get(act.name, []):
                            t.mute = True

    # ─── Sim Mix → wiggle_influence 적용 ────────────────────
    # [수정] wiggle_stiff/damp 는 절대 건드리지 않음.
    # wiggle_influence 만 갱신 (물리 블렌드 비율 전용).
    physics_strength = 0.0
    if active_layer and active_layer.type == 'SIM':
        physics_strength = active_layer.sim_mix

    for pb in obj.pose.bones:
        # wiggle 활성화된 본에만 적용
        if getattr(pb, "wiggle_tail", False) or getattr(pb, "wiggle_head", False):
            try:
                pb.wiggle_influence = physics_strength
            except Exception:
                pass

    if obj.id_data:
        obj.id_data.update_tag()


def update_layer_params(self, context):
    obj = context.object
    if obj:
        sync_combined_result(obj)
    if context.screen:
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def update_layer_selection(self, context):
    obj = context.object
    if not obj or not obj.animation_data:
        return
    idx = getattr(obj, "wiggle_layer_index", -1)
    if hasattr(obj, "wiggle_layers") and 0 <= idx < len(obj.wiggle_layers):
        act = bpy.data.actions.get(obj.wiggle_layers[idx].action_name)
        if act:
            obj.animation_data.action = act
        sync_combined_result(obj)


class WiggleSimLayer(bpy.types.PropertyGroup):
    name:        bpy.props.StringProperty(name="Layer Name", default="New Layer")
    action_name: bpy.props.StringProperty(name="Action Data")
    type:        bpy.props.EnumProperty(
        items=[('BASE', "Base (Anim)", ""), ('SIM', "Simulation", "")],
        name="Type", default='SIM'
    )
    influence: bpy.props.FloatProperty(name="Weight",   default=1.0, min=0.0, max=1.0, update=update_layer_params)
    sim_mix:   bpy.props.FloatProperty(name="Sim Mix",  default=1.0, min=0.0, max=1.0, update=update_layer_params)
    mute:      bpy.props.BoolProperty(name="Mute",      default=False, update=update_layer_params)


class WIGGLE_UL_SimMixLayers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        row = layout.row(align=True)
        row.prop(item, "mute", text="",
                 icon='CHECKBOX_DEHLT' if item.mute else 'CHECKBOX_HLT', emboss=False)
        row.label(text="", icon='ANIM' if item.type == 'BASE' else 'PHYSICS')
        row.prop(item, "name", text="", emboss=False)
        row.label(text=f"{int(item.influence * 100)}%")


class WIGGLE_OT_LayerAction(bpy.types.Operator):
    bl_idname  = "wiggle.layer_action"
    bl_label   = "Layer Action"
    bl_options = {'REGISTER', 'UNDO'}
    action: bpy.props.EnumProperty(
        items=[('ADD','Add',''),('REMOVE','Remove',''),('UP','Up',''),('DOWN','Down','')]
    )

    def execute(self, context):
        obj = context.object
        if not obj or obj.type != 'ARMATURE':
            return {'CANCELLED'}
        layers = obj.wiggle_layers
        idx    = obj.wiggle_layer_index

        if self.action == 'ADD':
            l = layers.add()
            l.name = f"Layer {len(layers)}"
            obj.wiggle_layer_index = len(layers) - 1
        elif self.action == 'REMOVE' and 0 <= idx < len(layers):
            layers.remove(idx)
            obj.wiggle_layer_index = max(0, idx - 1)
        elif self.action == 'UP' and idx > 0:
            layers.move(idx, idx - 1)
            obj.wiggle_layer_index -= 1
        elif self.action == 'DOWN' and idx < len(layers) - 1:
            layers.move(idx, idx + 1)
            obj.wiggle_layer_index += 1

        return {'FINISHED'}


class WIGGLE_OT_BakeCombined(bpy.types.Operator):
    bl_idname   = "wiggle.bake_combined"
    bl_label    = "Bake Combined"
    bl_options  = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj   = context.object
        scene = context.scene
        if not obj or obj.type != 'ARMATURE':
            return {'CANCELLED'}

        bake_bones = [
            b for b in obj.pose.bones
            if (getattr(b, "wiggle_head", False) or getattr(b, "wiggle_tail", False))
            and not getattr(b, "wiggle_mute", False)
        ]
        if not bake_bones:
            self.report({'WARNING'}, "No bones to bake.")
            return {'CANCELLED'}

        start_frame = scene.frame_start
        end_frame   = scene.frame_end
        orig_frame  = scene.frame_current
        blend_frames = max(0, int((end_frame - start_frame) * 0.2))

        # [수정] Bake 패널의 preroll/bake_overwrite/bake_nla 설정이 이 오퍼레이터가
        # UI에서 실제로 호출되는 쪽(wiggle.bake_combined)임에도 전혀 읽히지 않던
        # 문제 수정. wiggle.bake(WiggleBake)에 있던 로직을 그대로 가져옴.
        w = scene.wiggle

        if obj.mode != 'POSE':
            bpy.ops.object.mode_set(mode='POSE')
        if not obj.animation_data:
            obj.animation_data_create()

        if getattr(w, "bake_nla", False) and obj.animation_data.action:
            old_action = obj.animation_data.action
            track = obj.animation_data.nla_tracks.new()
            track.name = "WGL_PrevAction"
            track.strips.new(old_action.name, int(old_action.frame_range[0]), old_action)
            obj.animation_data.action = None

        if not getattr(w, "bake_overwrite", False):
            new_action = bpy.data.actions.new(name="Wiggle_Baked_Combined")
            obj.animation_data.action = new_action

        if getattr(w, "preroll", 0) > 0:
            w.is_preroll = True
            preroll_start = start_frame - max(1, w.preroll)
            for f in range(preroll_start, start_frame):
                scene.frame_set(f)
                context.view_layer.update()
            w.is_preroll = False

        for f in range(start_frame, end_frame + 1):
            scene.frame_set(f)
            context.view_layer.update()
            for pb in bake_bones:
                pb.keyframe_insert(data_path="location", group=pb.name)
                if pb.rotation_mode == 'QUATERNION':
                    pb.keyframe_insert(data_path="rotation_quaternion", group=pb.name)
                elif pb.rotation_mode == 'AXIS_ANGLE':
                    pb.keyframe_insert(data_path="rotation_axis_angle", group=pb.name)
                else:
                    pb.keyframe_insert(data_path="rotation_euler", group=pb.name)
                pb.keyframe_insert(data_path="scale", group=pb.name)

        if obj.animation_data and obj.animation_data.action:
            action     = obj.animation_data.action
            if not getattr(w, "bake_overwrite", False):
                action.name = "Wiggle_Baked_Combined"
            bone_names = {b.name for b in bake_bones}
            curves     = getattr(action, "curves", getattr(action, "fcurves", []))
            for fc in curves:
                if any(f'["{n}"]' in fc.data_path for n in bone_names):
                    sv = fc.evaluate(start_frame)
                    if blend_frames > 0:
                        for f in range(end_frame - blend_frames, end_frame + 1):
                            t = (f - (end_frame - blend_frames)) / blend_frames
                            blended = fc.evaluate(f) * (1.0 - t) + sv * t
                            fc.keyframe_points.insert(f, blended, options={'FAST'})
                    for kp in fc.keyframe_points:
                        kp.interpolation = 'BEZIER'
                        kp.handle_left_type = kp.handle_right_type = 'AUTO'
                    fc.update()

        for track in obj.animation_data.nla_tracks:
            if track.name.startswith("WGL_Trk_"):
                track.mute = True

        obj.wiggle_freeze = True
        scene.frame_set(orig_frame)
        self.report({'INFO'}, f"Bake Complete: {start_frame}~{end_frame}")
        return {'FINISHED'}


classes = (
    WiggleSimLayer,
    WIGGLE_UL_SimMixLayers,
    WIGGLE_OT_LayerAction,
    WIGGLE_OT_BakeCombined,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Object.wiggle_layers = bpy.props.CollectionProperty(type=WiggleSimLayer)
    bpy.types.Object.wiggle_layer_index = bpy.props.IntProperty(
        name="Idx", default=0, update=update_layer_selection
    )
    # wiggle_influence: 기본값 1.0 (물리 100% 적용)
    # 0.0 이면 등록 직후 물리가 전혀 작동 안 하는 버그 방지
    if not hasattr(bpy.types.PoseBone, "wiggle_influence"):
        bpy.types.PoseBone.wiggle_influence = bpy.props.FloatProperty(
            name="Wiggle Influence",
            description="Physics blend ratio (0=Animation only, 1=Full physics)",
            default=1.0, min=0.0, max=1.0
        )

    if wiggle_frame_change_handler not in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.append(wiggle_frame_change_handler)


def unregister():
    if wiggle_frame_change_handler in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.remove(wiggle_frame_change_handler)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    for attr in ("wiggle_layers", "wiggle_layer_index"):
        if hasattr(bpy.types.Object, attr):
            delattr(bpy.types.Object, attr)
    if hasattr(bpy.types.PoseBone, "wiggle_influence"):
        delattr(bpy.types.PoseBone, "wiggle_influence")


if __name__ == "__main__":
    register()
