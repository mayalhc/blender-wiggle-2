import bpy
from bpy.app.handlers import persistent

# --- GLOBAL RECURSION GUARD ---
_sync_in_progress = False

# --- NLA INFLUENCE CLEANUP (Blender NLA 핵심 해결책) ---
def use_animated_influence(strip):
    """NLA strip의 influence를 Python에서 강제로 static 값으로 만들기"""
    if strip.use_animated_influence:
        return
        
    strip.use_animated_influence = True
    
    # 💡 [수정 완료] NlaStrip에는 fcurves가 직접 존재하지 않으므로 
    # 블렌더 애니메이션 데이터 내 주소 ID 및 액션 F-커브 구조를 안전하게 역추적하여 초기화합니다.
    if strip.action and hasattr(strip.action, "fcurves"):
        for fcu in strip.action.fcurves:
            if "influence" in fcu.data_path:
                strip.action.fcurves.remove(fcu)
                
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
    except Exception as e:
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
    existing_action = None
    
    # NLA 트랙 안전 조회
    nla_tracks = getattr(obj.animation_data, "nla_tracks", [])
    for track in nla_tracks:
        if track.name != "WGL_Base" and not track.name.startswith("WGL_Trk_"):
            first_strip = next((s for s in track.strips), None)
            if first_strip and first_strip.action:
                existing_action = first_strip.action
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
            if base_layer and hasattr(base_layer, "action_name") and base_layer.action_name:
                target_action = bpy.data.actions.get(base_layer.action_name)
                
        if not target_action and base_track:
            first_strip = next((s for s in base_track.strips), None)
            if first_strip and first_strip.action:
                target_action = first_strip.action
                
    if target_action:
        if not base_track:
            base_track = obj.animation_data.nla_tracks.new()
            base_track.name = "WGL_Base"
        else:
            base_track.name = "WGL_Base"
            
        base_track.lock = False
        base_track.mute = False
        
        if not has_strips:
            # 💡 [수정 완료] context 프레임 참조 오류 방지 가드 가미
            start_fr = bpy.context.scene.frame_start if bpy.context.scene else 1.0
            strip = base_track.strips.new(target_action.name, int(start_fr), target_action)
            strip.blend_type = 'REPLACE'
            strip.extrapolation = 'HOLD'
            strip.influence = 1.0
            use_animated_influence(strip)
            
        if existing_action:
            obj.animation_data.action = existing_action
        else:
            if obj.animation_data.action == target_action:
                obj.animation_data.action = None
                
        all_base_tracks = [t for t in obj.animation_data.nla_tracks if t.name.startswith("WGL_Base")]
        if len(all_base_tracks) > 1:
            for extra_track in all_base_tracks[1:]:
                obj.animation_data.nla_tracks.remove(extra_track)
                
        # 💡 [수정 완료] 존재하지 않는 속성인 is_tweakmode를 제거하고 use_tweak_mode 규칙으로 정상 일치화했습니다.
        if hasattr(obj.animation_data, "use_tweak_mode") and obj.animation_data.use_tweak_mode:
            obj.animation_data.use_tweak_mode = False
            
        if existing_action and existing_action.frame_range:
            act_start, act_end = existing_action.frame_range
            if bpy.context.scene:
                bpy.context.scene.frame_start = int(act_start)
                bpy.context.scene.frame_end = int(act_end)
                
        if base_track:
            user_tracks = [t for t in obj.animation_data.nla_tracks if t.name != "WGL_Base" and not t.name.startswith("WGL_Trk_")]
            if user_tracks:
                for strip in base_track.strips:
                    strip.use_auto_blend = False

    # === [최적화 수정] 매 프레임 탐색 성능 향상을 위해 NLA 트랙을 액션 이름 기준으로 사전 매핑합니다.
    nla_tracks = getattr(obj.animation_data, "nla_tracks", [])
    action_to_tracks = {}
    for track in nla_tracks:
        for strip in track.strips:
            if strip.action:
                action_to_tracks.setdefault(strip.action.name, []).append(track)

    # 2. 모든 레이어 기본 상태 설정
    for layer in layers:
        if not layer.action_name:
            continue
        target_action = bpy.data.actions.get(layer.action_name)
        if not target_action:
            continue
            
        # 사전(Dict) 캐시를 활용해 중복 루프를 제거하고 다이렉트로 트랙을 제어합니다.
        matched_tracks = action_to_tracks.get(target_action.name, [])
        for track in matched_tracks:
            track.mute = layer.mute
            for strip in track.strips:
                strip.blend_type = 'REPLACE' if layer.type == 'BASE' else 'COMBINE'
                strip.extrapolation = 'HOLD'

    # 3. Layer Weight 블렌드 (NLA 전용 크로스페이드)
    if active_layer:
        if active_layer.type == 'BASE':
            # Base 레이어 선택 상태 → 모든 Sim 레이어를 완전 음소거(Mute) 처리
            for layer in layers:
                if layer.type == 'SIM' and layer.action_name:
                    t_act = bpy.data.actions.get(layer.action_name)
                    if t_act:
                        for track in action_to_tracks.get(t_act.name, []):
                            track.mute = True
                            
            # 선택된 Base 레이어 활성화
            if active_layer.action_name:
                base_act = bpy.data.actions.get(active_layer.action_name)
                if base_act:
                    for track in action_to_tracks.get(base_act.name, []):
                        track.mute = False
                        for strip in track.strips:
                            strip.influence = 1.0
                            use_animated_influence(strip) # 인플루언스 락 가드
        else:
            # Sim 레이어 선택 상태 → Layer Weight 값에 맞춰 Base와 Sim 간의 가중치 교차 감쇠(Cross-fade)
            weight = active_layer.influence
            base_influence = max(0.0, min(1.0, 1.0 - weight)) # 안전한 범위 클램핑
            
            # Base 레이어 가중치 적용
            base_layer = next((l for l in layers if l.type == 'BASE'), None)
            if base_layer and base_layer.action_name:
                base_act = bpy.data.actions.get(base_layer.action_name)
                if base_act:
                    for track in action_to_tracks.get(base_act.name, []):
                        track.mute = False
                        for strip in track.strips:
                            strip.influence = base_influence
                            use_animated_influence(strip)
                            
            # 현재 선택된 대상 Sim 레이어 가중치 적용
            if active_layer.action_name:
                sim_act = bpy.data.actions.get(active_layer.action_name)
                if sim_act:
                    for track in action_to_tracks.get(sim_act.name, []):
                        if weight <= 0.0001:
                            track.mute = True
                        else:
                            track.mute = False
                            for strip in track.strips:
                                strip.influence = weight
                                use_animated_influence(strip)
                                
            # 선택되지 않은 다른 나머지 모든 Sim 레이어들은 일괄 OFF(음소거)
            for layer in layers:
                if layer.type == 'SIM' and layer != active_layer and layer.action_name:
                    other_act = bpy.data.actions.get(layer.action_name)
                    if other_act:
                        for track in action_to_tracks.get(other_act.name, []):
                            track.mute = True

# 4. Sim Mix (Physics) — 완전 독립 제어
    physics_strength = 0.0
    if active_layer and active_layer.type == 'SIM':
        physics_strength = active_layer.sim_mix        
    for pb in obj.pose.bones:
        pb.wiggle_influence = physics_strength        
        if hasattr(pb, "wiggle_stiff"):
            pb.wiggle_stiff = (1.0 - pb.wiggle_influence) * 80.0
    if obj.id_data:
        obj.id_data.update_tag()



def update_layer_params(self, context):
    """슬라이더 변경 시 즉시 sync 및 3D 뷰포트 안전 갱신"""
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
        active_layer = obj.wiggle_layers[idx]
        target_action = bpy.data.actions.get(active_layer.action_name)
        if target_action:
            obj.animation_data.action = target_action
        sync_combined_result(obj)

class WiggleSimLayer(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Layer Name", default="New Layer")
    action_name: bpy.props.StringProperty(name="Action Data")
    type: bpy.props.EnumProperty(
        items=[('BASE', "Base (Anim)", ""), ('SIM', "Simulation", "")],
        name="Type",
        default='SIM'
    )
    influence: bpy.props.FloatProperty(name="Weight", default=1.0, min=0.0, max=1.0, update=update_layer_params)
    sim_mix: bpy.props.FloatProperty(name="Sim Mix", default=1.0, min=0.0, max=1.0, update=update_layer_params)
    mute: bpy.props.BoolProperty(name="Mute", default=False, update=update_layer_params)

# --- UI LIST ---
class WIGGLE_UL_SimMixLayers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        row = layout.row(align=True)

        row.prop(item, "mute", text="", icon='CHECKBOX_DEHLT' if item.mute else 'CHECKBOX_HLT', emboss=False)
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