import bpy
from bpy.app.handlers import persistent

# --- GLOBAL RECURSION GUARD ---
_sync_in_progress = False

# --- NLA INFLUENCE CLEANUP (Blender NLA 핵심 해결책) ---
def use_animated_influence(strip):
    """NLA strip의 influence를 Python에서 강제로 static 값으로 만들기
    - 첫 번째 키프레임(기본값)이 존재하면 제거
    - use_animated_influence = True로 설정하여 Blender가 Python influence를 존중하게 함"""
    if strip.use_animated_influence:
        return
    
    # 기존 animated influence 활성화
    strip.use_animated_influence = True
    
    # 첫 번째 FCurve(인플루언스 커브)에서 키프레임 제거
    if strip.fcurves and len(strip.fcurves) > 0:
        fcu = strip.fcurves[0]  # influence는 항상 첫 번째 FCurve
        if fcu.keyframe_points:
            # 첫 번째 키프레임만 제거 (Blender가 자동으로 생성한 기본 키)
            keyframes = fcu.keyframe_points
            if len(keyframes) > 0:
                keyframe = keyframes[0]
                keyframes.remove(keyframe)
    
    # static influence 강제 적용
    strip.influence = 1.0


# --- CORE FUNCTIONS ---

@persistent
def wiggle_frame_change_handler(scene):
    """매 프레임마다 Layer Weight + Sim Mix 적용 (recursion 방지)"""
    global _sync_in_progress
    if _sync_in_progress:
        return
    try:
        _sync_in_progress = True
        obj = bpy.context.object
        if obj and obj.type == 'ARMATURE' and hasattr(obj, "wiggle_layers") and obj.animation_data:
            if 0 <= getattr(obj, "wiggle_layer_index", -1) < len(obj.wiggle_layers):
                sync_combined_result(obj)
    except:
        pass
    finally:
        _sync_in_progress = False


def sync_combined_result(obj):
    """Layer Weight(NLA 블렌드) + Sim Mix(Physics) 완전 분리"""
    if not obj or not obj.animation_data:
        return

    layers = obj.wiggle_layers
    idx = obj.wiggle_layer_index
    active_layer = layers[idx] if 0 <= idx < len(layers) else None

    # 1. BASE NLA 트랙 자동 생성
    base_layer = next((l for l in layers if l.type == 'BASE'), None)
    if base_layer and base_layer.action_name:
        target_action = bpy.data.actions.get(base_layer.action_name)
        if target_action:
            has_base_track = any(
                any(s.action == target_action for s in track.strips)
                for track in obj.animation_data.nla_tracks
            )
            if not has_base_track:
                track = obj.animation_data.nla_tracks.new()
                track.name = "WGL_Base"
                strip = track.strips.new(target_action.name, int(bpy.context.scene.frame_start), target_action)
                strip.blend_type = 'REPLACE'
                strip.extrapolation = 'HOLD'
                strip.influence = 1.0
                use_animated_influence(strip)   # ← 핵심: influence cleanup

    # 2. 모든 레이어 기본 상태
    for layer in layers:
        if not layer.action_name:
            continue
        target_action = bpy.data.actions.get(layer.action_name)
        if not target_action:
            continue
        for track in obj.animation_data.nla_tracks:
            if any(s.action == target_action for s in track.strips):
                track.mute = layer.mute
                for strip in track.strips:
                    strip.blend_type = 'REPLACE' if layer.type == 'BASE' else 'COMBINE'
                    strip.extrapolation = 'HOLD'

    # 3. Layer Weight 블렌드 (NLA 전용)
    if active_layer:
        if active_layer.type == 'BASE':
            # Base 선택 → Sim 완전 mute
            for layer in layers:
                if layer.type == 'SIM' and layer.action_name:
                    target_action = bpy.data.actions.get(layer.action_name)
                    if target_action:
                        for track in obj.animation_data.nla_tracks:
                            if any(s.action == target_action for s in track.strips):
                                track.mute = True
            # Base ON
            target_action = bpy.data.actions.get(active_layer.action_name)
            if target_action:
                for track in obj.animation_data.nla_tracks:
                    if any(s.action == target_action for s in track.strips):
                        track.mute = False
                        for strip in track.strips:
                            strip.influence = 1.0
                            use_animated_influence(strip)   # ← 핵심 cleanup

        else:
            # Sim 선택 → Layer Weight로 Base/Sim cross-fade
            weight = active_layer.influence
            base_influence = 1.0 - weight

            # Base
            base_layer = next((l for l in layers if l.type == 'BASE'), None)
            if base_layer and base_layer.action_name:
                target_action = bpy.data.actions.get(base_layer.action_name)
                if target_action:
                    for track in obj.animation_data.nla_tracks:
                        if any(s.action == target_action for s in track.strips):
                            track.mute = False
                            for strip in track.strips:
                                strip.influence = base_influence
                                use_animated_influence(strip)   # ← 핵심 cleanup

            # 현재 Sim Layer
            target_action = bpy.data.actions.get(active_layer.action_name)
            if target_action:
                for track in obj.animation_data.nla_tracks:
                    if any(s.action == target_action for s in track.strips):
                        if weight <= 0.0001:
                            track.mute = True
                        else:
                            track.mute = False
                            for strip in track.strips:
                                strip.influence = weight
                                use_animated_influence(strip)   # ← 핵심 cleanup

            # 다른 Sim OFF
            for layer in layers:
                if layer.type == 'SIM' and layer != active_layer and layer.action_name:
                    target_action = bpy.data.actions.get(layer.action_name)
                    if target_action:
                        for track in obj.animation_data.nla_tracks:
                            if any(s.action == target_action for s in track.strips):
                                track.mute = True

    # 4. Sim Mix (Physics) — 완전 독립
    physics_strength = 0.0
    if active_layer and active_layer.type == 'SIM':
        physics_strength = active_layer.sim_mix

    for pb in obj.pose.bones:
        pb.wiggle_influence = physics_strength
        if hasattr(pb, "wiggle_stiffness"):
            pb.wiggle_stiffness = (1.0 - pb.wiggle_influence) * 80.0

    # viewport 안전 업데이트
    try:
        bpy.context.view_layer.update()
    except:
        pass


def update_layer_params(self, context):
    """슬라이더 변경 시 즉시 sync"""
    obj = context.object
    if obj:
        sync_combined_result(obj)
    if context.area:
        context.area.tag_redraw()
    # Layer Weight 슬라이더 변경 시 redraw
    try:
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    except:
        pass


def update_layer_selection(self, context):
    obj = context.object
    if not obj or not obj.animation_data:
        return

    idx = obj.wiggle_layer_index
    if 0 <= idx < len(obj.wiggle_layers):
        active_layer = obj.wiggle_layers[idx]
        target_action = bpy.data.actions.get(active_layer.action_name)
        if target_action:
            obj.animation_data.action = target_action

    sync_combined_result(obj)


# --- DATA STRUCTURE ---

class WiggleSimLayer(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Layer Name", default="New Layer")
    action_name: bpy.props.StringProperty(name="Action Data")
    type: bpy.props.EnumProperty(
        items=[('BASE', "Base (Anim)", ""), ('SIM', "Simulation", "")],
        name="Type", default='SIM'
    )
    influence: bpy.props.FloatProperty(name="Weight", default=1.0, min=0.0, max=1.0, update=update_layer_params)
    sim_mix: bpy.props.FloatProperty(name="Sim Mix", default=1.0, min=0.0, max=1.0, update=update_layer_params)
    mute: bpy.props.BoolProperty(name="Mute", default=False, update=update_layer_params)


# --- UI LIST ---

class WIGGLE_UL_SimMixLayers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        row = layout.row(align=True)
        row.prop(item, "mute", text="", icon='CHECKBOX_HLT' if not item.mute else 'CHECKBOX_DEHLT', emboss=False)
        row.label(text="", icon='ANIM' if item.type == 'BASE' else 'PHYSICS')
        row.prop(item, "name", text="", emboss=False)
        row.label(text=f"{int(item.influence * 100)}%")


# --- OPERATORS ---

class WIGGLE_OT_LayerAction(bpy.types.Operator):
    bl_idname = "wiggle.layer_action"
    bl_label = "Layer Action"
    bl_options = {'REGISTER', 'UNDO'}
    
    action: bpy.props.EnumProperty(
        items=[('ADD', "Add", ""), ('REMOVE', "Remove", ""), ('UP', "Up", ""), ('DOWN', "Down", "")]
    )

    def execute(self, context):
        obj = context.object
        if not obj or obj.type != 'ARMATURE':
            return {'CANCELLED'}

        if self.action == 'ADD':
            if not obj.animation_data:
                obj.animation_data_create()
            
            layer_count = len(obj.wiggle_layers)
            new_l = obj.wiggle_layers.add()
            
            if layer_count == 0:
                new_l.name = "Base_Anim"
                new_l.type = 'BASE'
                new_l.influence = 1.0
                
                # 1. 액션 이름 설정 및 할당
                if obj.animation_data.action:
                    new_l.action_name = obj.animation_data.action.name
                else:
                    new_act = bpy.data.actions.new("Base_Action")
                    new_l.action_name = new_act.name
                    obj.animation_data.action = new_act

                # 2. NLA 트랙 생성 및 스트립 추가 (푸시다운 실행)
                track = obj.animation_data.nla_tracks.new()
                track.name = "WGL_Base"
                target_act = bpy.data.actions.get(new_l.action_name)
                if target_act:
                    strip = track.strips.new(target_act.name, int(context.scene.frame_start), target_act)
                    strip.blend_type = 'REPLACE'
                    strip.extrapolation = 'HOLD'
                    strip.influence = 1.0
                    use_animated_influence(strip)

                # 3. [추가] 작업대 액션을 비움으로써 NLA가 활성화되게 함
                obj.animation_data.action = None

            else:
                new_l.name = f"Sim_Layer_{layer_count}"
                new_l.type = 'SIM'
                new_act = bpy.data.actions.new(name=f"Act_{new_l.name}")
                new_l.action_name = new_act.name

                track = obj.animation_data.nla_tracks.new()
                track.name = f"WGL_Trk_{new_l.name.replace(' ', '_')}"
                target_act = bpy.data.actions.get(new_l.action_name)
                if target_act:
                    strip = track.strips.new(target_act.name, int(context.scene.frame_start), target_act)
                    strip.blend_type = 'COMBINE'
                    strip.extrapolation = 'HOLD'

            obj.wiggle_layer_index = len(obj.wiggle_layers) - 1

        elif self.action == 'REMOVE':
            idx = obj.wiggle_layer_index
            if 0 <= idx < len(obj.wiggle_layers) and obj.wiggle_layers[idx].type != 'BASE':
                layer = obj.wiggle_layers[idx]
                target_action = bpy.data.actions.get(layer.action_name)
                for track in list(obj.animation_data.nla_tracks):
                    if any(s.action == target_action for s in track.strips):
                        obj.animation_data.nla_tracks.remove(track)
                        break
                
                obj.wiggle_layers.remove(idx)
                obj.wiggle_layer_index = max(0, idx - 1)

        elif self.action in {'UP', 'DOWN'}:
            idx = obj.wiggle_layer_index
            neighbor = idx - 1 if self.action == 'UP' else idx + 1
            if 0 <= neighbor < len(obj.wiggle_layers):
                obj.wiggle_layers.move(idx, neighbor)
                obj.wiggle_layer_index = neighbor

        sync_combined_result(obj)
        return {'FINISHED'}


class WIGGLE_OT_BakeCombined(bpy.types.Operator):
    bl_idname = "wiggle.bake_combined"
    bl_label = "Bake Combined Wiggle (Base + Sim)"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Bake by mixing keyframe layers with simulation layers"
    
    def execute(self, context):
        obj = context.object
        if not obj or obj.type != 'ARMATURE' or not obj.animation_data:
            return {'CANCELLED'}

        scene = context.scene
        # 루프 보정을 위한 프레임 정보 계산
        start_frame = scene.frame_start
        end_frame = scene.frame_end
        duration = end_frame - start_frame
        blend_frames = int(duration * 0.2) 

        sync_combined_result(obj)

        if context.object.mode != 'POSE':
            bpy.ops.object.mode_set(mode='POSE')

        # 베이크 대상 뼈 미리 파악
        bake_bones = [b for b in obj.pose.bones if (getattr(b, "wiggle_head", False) or getattr(b, "wiggle_tail", False))]

        bpy.ops.nla.bake(
            frame_start=start_frame,
            frame_end=end_frame,
            step=1,
            only_selected=False,
            bake_types={'POSE'},
            visual_keying=True,
            clear_constraints=False,
            use_current_action=False,
            clean_curves=True,
        )

        if obj.animation_data and obj.animation_data.action:
            action = obj.animation_data.action
            action.name = "Wiggle_Baked_Combined"

            # --- [추가] Seamless Loop 보정 로직 ---
            bone_names = {b.name for b in bake_bones}
            curves = getattr(action, "curves", getattr(action, "fcurves", []))
            
            for fc in curves:
                if any(f'["{name}"]' in fc.data_path for name in bone_names):
                    start_val = fc.evaluate(start_frame)
                    if blend_frames > 0:
                        for f in range(end_frame - blend_frames, end_frame + 1):
                            t = (f - (end_frame - blend_frames)) / blend_frames
                            current_val = fc.evaluate(f)
                            blended_val = current_val * (1.0 - t) + start_val * t
                            fc.keyframe_points.insert(f, blended_val, options={'FAST'})
                    
                    for kp in fc.keyframe_points:
                        kp.interpolation = 'BEZIER'
                        kp.handle_left_type = 'AUTO'
                        kp.handle_right_type = 'AUTO'
                    fc.update()

        for track in obj.animation_data.nla_tracks:
            if track.name.startswith("WGL_Trk_"):
                track.mute = True
           
        # --- 알려주신 속성명으로 시뮬레이션 OFF ---
        # 1. 씬 전체 제어
        if hasattr(scene, "wiggle"):
            scene.wiggle.wiggle_mute = True    # Mute 켬
            scene.wiggle.wiggle_enable = False # Enable 끔
        
        # 2. 오브젝트 및 뼈 단위 제어
        obj.wiggle_freeze = True
        
        for pbone in bake_bones:
            if hasattr(pbone, "wiggle_mute"):
                pbone.wiggle_mute = True
            if hasattr(pbone, "wiggle_enable"):
                pbone.wiggle_enable = False
        # --------------------------------------

        self.report({'INFO'}, f"Combined Bake Complete: Seamless Loop Applied & Simulation Muted")
        return {'FINISHED'}


# --- REGISTRATION ---

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
    bpy.types.PoseBone.wiggle_influence = bpy.props.FloatProperty(name="Wiggle Influence", default=0.0)

    if wiggle_frame_change_handler not in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.append(wiggle_frame_change_handler)


def unregister():
    if wiggle_frame_change_handler in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.remove(wiggle_frame_change_handler)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    
    del bpy.types.Object.wiggle_layers
    del bpy.types.Object.wiggle_layer_index
    del bpy.types.PoseBone.wiggle_influence


if __name__ == "__main__":
    register()