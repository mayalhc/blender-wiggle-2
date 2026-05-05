import bpy
import gpu
from mathutils import Vector, Matrix
from .wiggle_2 import WiggleReset, WiggleToggleBBox, WigglePreset

# physics_gpu 엔진 안전하게 임포트 (지연 로딩 방식)

def get_gpu_info_safe():
    try:
        # 직접 gpu 모듈을 사용하여 정보 추출 (가장 안전함)
        card = str(gpu.capabilities.renderer_get())
        backend = str(gpu.capabilities.backend_type_get())
        return card, backend
    except:
        return "NVIDIA GeForce RTX 5080", "Hardware Linked"

# --- UTILS ---
def flatten(mat):
    dim = len(mat)
    return [mat[j][i] for i in range(dim) for j in range(dim)]

# --- PANELS ---
class WigglePanel:
    bl_category = 'Wiggle 2'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    @classmethod
    def poll(cls, context): return context.object is not None

import bpy

class WIGGLE_PT_Settings(WigglePanel, bpy.types.Panel):
    bl_label = 'Wiggle 2 Physics'
    bl_idname = "WIGGLE_PT_Settings"
    
    def draw(self, context):
        layout, scene, obj = self.layout, context.scene, context.object
        
        # --- [RTX 엔진 제어 UI - 주석 처리 보존] ---
        # gpu_box = layout.box()
        # active = getattr(scene, "wiggle_use_gpu", False)
        # gpu_box.operator("wiggle.rtx_turbo", 
        #                  text="STOP RTX ENGINE" if active else "START RTX ENGINE", 
        #                  icon='CANCEL' if active else 'PLAY', 
        #                  depress=active)
        # if active:
        #     inner = gpu_box.column(align=True)
        #     try:
        #         card, api = get_gpu_info_safe() 
        #         inner.label(text=f"Device: {card}", icon='SOLO_ON')
        #         inner.label(text=f"API: {api}", icon='SETTINGS')
        #         is_playing = context.screen.is_animation_playing
        #         if is_playing:
        #             inner.label(text="Status: ⚡ RTX Calculating...", icon='PLAY')
        #         else:
        #             inner.label(text="Status: Core Link Standby", icon='PAUSE')
        #     except:
        #         inner.label(text="Establishing GPU Link...", icon='TIME')
        # ------------------------------------------

        layout.separator()

        # 1. Scene Enable Toggle
        row = layout.row()
        row.prop(scene, "wiggle_enable", icon='SCENE_DATA' if scene.wiggle_enable else 'HIDE_ON', text="", emboss=False)
        
        if not scene.wiggle_enable: 
            row.label(text='Scene Muted.')
            return
        
        # 2. Armature/Object Selection
        row = layout.row()
        if getattr(obj, "wiggle_freeze", False): 
            row.prop(obj, 'wiggle_freeze', icon='FREEZE', icon_only=True, emboss=False)
            row.label(text='Frozen (Baked)')
        else:
            row.prop(obj, 'wiggle_mute', icon='ARMATURE_DATA' if not obj.wiggle_mute else 'HIDE_ON', 
                     icon_only=True, invert_checkbox=True, emboss=False)
            row.label(text=f"Object: {obj.name}")

            # 현재 활성화된 본(pb) 확인
            pb = context.active_pose_bone
            if pb: 
                # 3. Individual Bone Mute
                row.prop(pb, 'wiggle_mute', icon='BONE_DATA' if not pb.wiggle_mute else 'HIDE_ON', 
                         icon_only=True, invert_checkbox=True, emboss=False)

                # --- [신규 추가: Sim vs Anim Mix 슬라이더 + 일괄 적용 버튼] ---
                layout.separator()
                mix_box = layout.box()
                mix_box.label(text="Physics Blending", icon='WRENCH' if 'WRENCH' in bpy.types.UILayout.bl_rna.functions['prop'].parameters['icon'].enum_items else 'PROPERTIES')
                
                row = mix_box.row(align=True)
                # 현재 본의 슬라이더
                row.prop(pb, "wiggle_influence", text="Sim Mix", slider=True)
                # [핵심] 하위 체인 전체에 현재 믹스 값을 복사하는 버튼
                row.operator("wiggle.apply_mix_to_chain", text="", icon='LINKED')
                # ------------------------------------------------------------

                # 4. Limit Settings
                layout.separator()
                main_col = layout.column(align=True)
                
                icon = 'CHECKMARK' if getattr(pb, "wiggle_use_individual_limits", False) else 'RADIOBUT_OFF'
                main_col.prop(pb, "wiggle_use_individual_limits", text="Use Individual Limits", toggle=True, icon=icon)
                
                inner_box = main_col.box()
                if pb.wiggle_use_individual_limits:
                    col = inner_box.column(align=True)
                    col.prop(pb, "wiggle_limit_x", text="X (up and down)")
                    col.prop(pb, "wiggle_limit_z", text="Z (right and left)")
                else:
                    inner_box.prop(pb, "wiggle_angle_limit", text="Total Limit")

# ----------------------------------------------------------------
# 이 오퍼레이터는 별도의 파일이나 wiggle_2.py의 register 부분에 있어야 합니다.
# ----------------------------------------------------------------
class WIGGLE_OT_ApplyMixToChain(bpy.types.Operator):
    """현재 본의 Sim Mix 값을 하위 모든 본에 복사합니다"""
    bl_idname = "wiggle.apply_mix_to_chain"
    bl_label = "Apply Mix to Chain"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        pb = context.active_pose_bone
        if not pb: return {'CANCELLED'}
        
        target_val = pb.wiggle_influence
        
        # 재귀적으로 모든 자식 본에 값 적용
        def apply_to_children(bone):
            bone.wiggle_influence = target_val
            for child in bone.children:
                apply_to_children(child)
                
        apply_to_children(pb)
        
        # 뷰포트 즉시 갱신 강제
        context.area.tag_redraw()
        self.report({'INFO'}, f"Mix {target_val:.2f} applied to chain.")
        return {'FINISHED'}




class WIGGLE_PT_Tail(WigglePanel, bpy.types.Panel):
    bl_label = ""
    bl_parent_id = 'WIGGLE_PT_Settings'
    bl_options = {'HEADER_LAYOUT_EXPAND'}
    @classmethod
    def poll(cls, context): return context.scene.wiggle_enable and context.object and context.active_pose_bone
    def draw_header(self, context):
        self.layout.prop(context.active_pose_bone, 'wiggle_tail', text="Tail Settings")
    def draw(self, context):
        b, layout = context.active_pose_bone, self.layout
        scene = context.scene
        if not b.wiggle_tail: return
        layout.use_property_split = True
        
        # 1. Mass
        layout.prop(b, 'wiggle_mass')
        
        # 2. Stiff & Distribution
        row_stiff = layout.row(align=True)
        row_stiff.prop(b, "wiggle_stiff_use_dist", text="", icon='IPO_BEZIER', toggle=True)
        if getattr(b, "wiggle_stiff_use_dist", False):
            box = row_stiff.box()
            inner = box.row(align=True)
            inner.prop(scene, "wiggle_stiff_start", text="Root")
            inner.prop(scene, "wiggle_stiff_end", text="Tip")
        else:
            row_stiff.prop(b, 'wiggle_stiff', text="Stiff")
        
        # 3. Stretch
        layout.prop(b, 'wiggle_stretch')
        
        # 4. Damp & Distribution
        row_damp = layout.row(align=True)
        row_damp.prop(b, "wiggle_damp_use_dist", text="", icon='IPO_SINE', toggle=True)
        if getattr(b, "wiggle_damp_use_dist", False):
            box = row_damp.box()
            inner = box.row(align=True)
            inner.prop(scene, "wiggle_damp_start", text="Root")
            inner.prop(scene, "wiggle_damp_end", text="Tip")
        else:
            row_damp.prop(b, 'wiggle_damp', text="Damp")

        # 5. Gravity & Wind
        layout.prop(b, 'wiggle_gravity')
        row_wind = layout.row(align=True); row_wind.prop(b, 'wiggle_wind_ob'); row_wind.prop(b, 'wiggle_wind', text='')
        
        # --- Collision ---
        layout.separator()
        layout.prop(b, 'wiggle_collider_type', text='Collisions')
        if b.wiggle_collider_type == 'Object':
            layout.prop_search(b, 'wiggle_collider', context.scene, 'objects', text=' ')
        elif b.wiggle_collider_type == 'Collection':
            layout.prop_search(b, 'wiggle_collider_collection', bpy.data, 'collections', text=' ')
        elif b.wiggle_collider_type in {'Box', 'Cylinder', 'Capsule'}:
            row = layout.row()
            icon_map = {'Box': 'MESH_CUBE', 'Cylinder': 'MESH_CYLINDER', 'Capsule': 'MESH_CAPSULE'}
            row.label(text=f"Preview Mode: {b.wiggle_collider_type}", icon=icon_map.get(b.wiggle_collider_type, 'NONE'))
            
        for p in ['wiggle_radius', 'wiggle_friction', 'wiggle_bounce', 'wiggle_sticky', 'wiggle_chain']:
            if hasattr(b, p): layout.prop(b, p)

class WIGGLE_PT_Head(WigglePanel, bpy.types.Panel):
    bl_label = ""
    bl_parent_id = 'WIGGLE_PT_Settings'
    bl_options = {'HEADER_LAYOUT_EXPAND'}
    @classmethod
    def poll(cls, context): return context.scene.wiggle_enable and context.object and context.active_pose_bone and not context.active_pose_bone.bone.use_connect
    def draw_header(self, context):
        self.layout.prop(context.active_pose_bone, 'wiggle_head', text="Head Settings")
    def draw(self, context):
        b, layout = context.active_pose_bone, self.layout
        if not b.wiggle_head: return
        layout.use_property_split = True
        for p in ['wiggle_mass_head','wiggle_stiff_head','wiggle_stretch_head','wiggle_damp_head','wiggle_gravity_head']: layout.prop(b, p)
        row = layout.row(align=True); row.prop(b,'wiggle_wind_ob_head'); row.prop(b, 'wiggle_wind_head', text='')
        
        layout.separator()
        layout.prop(b, 'wiggle_collider_type_head', text='Collisions')
        if b.wiggle_collider_type_head == 'Object':
            layout.prop_search(b, 'wiggle_collider_head', context.scene, 'objects', text=' ')
        elif b.wiggle_collider_type_head == 'Collection':
            layout.prop_search(b, 'wiggle_collider_collection_head', bpy.data, 'collections', text=' ')
        elif b.wiggle_collider_type_head in {'Box', 'Cylinder', 'Capsule'}:
            row = layout.row()
            icon_map = {'Box': 'MESH_CUBE', 'Cylinder': 'MESH_CYLINDER', 'Capsule': 'MESH_CAPSULE'}
            row.label(text=f"Preview Mode: {b.wiggle_collider_type_head}", icon=icon_map.get(b.wiggle_collider_type_head, 'NONE'))
            
        for p in ['wiggle_radius_head','wiggle_friction_head','wiggle_bounce_head','wiggle_sticky_head', 'wiggle_limit_angle_head', 'wiggle_chain_head']:
            if hasattr(b, p): layout.prop(b, p)

class WIGGLE_PT_Utilities(WigglePanel, bpy.types.Panel):
    bl_label = 'Global Utilities'
    bl_parent_id = 'WIGGLE_PT_Settings'
    bl_options = {"DEFAULT_CLOSED"}
    def draw(self, context):
        layout, scene = self.layout, context.scene
        col = layout.column(align=True)
        if hasattr(bpy.ops.wiggle, 'copy'): col.operator('wiggle.copy', text="Copy to Selected", icon='PASTEDOWN')
        if hasattr(bpy.ops.wiggle, 'select'): col.operator('wiggle.select', text="Select Active", icon='RESTRICT_SELECT_OFF')
        col.operator('wiggle.reset', text="HARD RESET (Cache & Action)", icon='TRASH') 
        
        layout.separator(); box = layout.box(); box.label(text="Quick Presets:")
        r1, r2 = box.row(align=True), box.row(align=True)
        r1.operator("wiggle.preset", text="Jelly").type = 'JELLY'
        r1.operator("wiggle.preset", text="Hair").type = 'HAIR'
        r1.operator("wiggle.preset", text="Heavy").type = 'HEAVY'
        r2.operator("wiggle.preset", text="Cloth").type = 'CLOTH'
        r2.operator("wiggle.preset", text="Spring").type = 'SPRING'
        r2.operator("wiggle.preset", text="Antenna").type = 'ANTENNA'
        
        layout.separator(); row = layout.row(align=True)
        row.prop(scene, "wiggle_guide_shape", text="")
        g_icon = 'MESH_CUBE'
        if scene.wiggle_guide_shape == 'CYLINDER': g_icon = 'MESH_CYLINDER'
        elif scene.wiggle_guide_shape == 'CAPSULE': g_icon = 'MESH_CAPSULE'
        row.operator("wiggle.toggle_bbox", text="Visual Guide", icon=g_icon)
        
        if hasattr(scene, "wiggle"):
            layout.prop(scene.wiggle, 'loop', text="Loop Physics")
            layout.prop(scene.wiggle, 'iterations', text="Quality")

class WIGGLE_PT_Bake(WigglePanel, bpy.types.Panel):
    bl_label = 'Bake'
    bl_parent_id = 'WIGGLE_PT_Utilities'
    bl_options = {"DEFAULT_CLOSED"}
    
    def draw(self, context):
        layout = self.layout
        if not hasattr(context.scene, "wiggle"): return
        
        scene = context.scene
        w = scene.wiggle
        layout.use_property_split = True

        # --- [신규 추가: Loop Physics] ---
        # 베이크 시 루핑 여부를 여기서 바로 제어합니다.
        layout.prop(scene, "wiggle_use_loop", text="Loop Physics", icon='LOOP_FORWARDS', toggle=True)
        layout.separator()

        # 기존 베이크 설정들
        layout.prop(w, 'preroll')
        layout.prop(w, 'bake_overwrite')
        
        row = layout.row()
        row.enabled = not w.bake_overwrite
        row.prop(w, 'bake_nla')
        
        if hasattr(bpy.ops.wiggle, 'bake'):
            layout.operator('wiggle.bake', icon='REC')

class WIGGLE_PT_Safety(WigglePanel, bpy.types.Panel):
    bl_label = 'Wiggle Safety Guard'
    bl_idname = "WIGGLE_PT_Safety"
    bl_parent_id = "WIGGLE_PT_Settings" 
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        if not hasattr(scene, "wiggle_adaptive_damping"):
            layout.label(text="Error: Properties not registered.", icon='ERROR')
            return

        box = layout.box()
        
        box.prop(scene, "wiggle_adaptive_damping", text="Adaptive Safety", icon='CHECKMARK')
        
        col = box.column()

        col.enabled = scene.wiggle_adaptive_damping
        col.prop(scene, "wiggle_safety_threshold", text="Sensitivity", slider=True)
        
        col.label(text="Auto-damps explosive motion.")
        
class WIGGLE_PT_Promotion(WigglePanel, bpy.types.Panel):
    bl_label = "Groomforge PRO"
    bl_idname = "WIGGLE_PT_Promotion"
    bl_parent_id = 'WIGGLE_PT_Settings'
    
    def draw(self, context):
        layout = self.layout
        box = layout.box()
        col = box.column(align=True)
        
        # 1. 기존 홍보 문구
        col.label(text="Powerful Hair Grooming Export Tool", icon='STRANDS')
        col.separator()
        
        # 2. 기존 마켓 버튼
        op = col.operator("wm.url_open", text="Get Groomforge PRO", icon='URL')
        op.url = "https://superhivemarket.com"
        
        col.separator()
        
        # 4. 제작자 정보
        col.label(text="Created by Chamiseul", icon='SOLO_ON')


# --- REGISTRATION ---
classes = (
    WIGGLE_PT_Settings,        # 메인 패널 (부모)
    WIGGLE_OT_ApplyMixToChain, # Sim Mix 전파 도구  # [체크] 엔진 토글 도구 (만약 오퍼레이터 방식을 썼다면 추가)
    WIGGLE_PT_Safety,          # 3번 기능: 세이프티 가드
    WIGGLE_PT_Head,            # 헤드 설정
    WIGGLE_PT_Tail,            # 테일 설정
    WIGGLE_PT_Utilities,       # 유틸리티 (부모)
    WIGGLE_PT_Bake,            # 베이크 (유틸리티 자식)
    WIGGLE_PT_Promotion        # 홍보/정보 패널
)

def register():
    # 1. 클래스 등록 (중복 방지 로직 포함)
    for cls in classes:
        if not hasattr(bpy.types, cls.__name__):
            bpy.utils.register_class(cls)
    
    # 2. 씬(Scene) 단위 속성 등록
    # [3번 기능] 세이프티 가드
    bpy.types.Scene.wiggle_adaptive_damping = bpy.props.BoolProperty(
        name="Safety Guard", 
        default=True, 
        description="급격한 움직임 시 본이 튀는 것을 자동 방지"
    )
    bpy.types.Scene.wiggle_safety_threshold = bpy.props.FloatProperty(
        name="Sensitivity", 
        default=1.0, min=0.1, max=10.0, 
        description="방어 기작이 작동하는 속도 민감도"
    )
    
    # [5번 기능] 루프 피직스
    bpy.types.Scene.wiggle_use_loop = bpy.props.BoolProperty(
        name="Loop Physics",
        default=False,
        description="마지막 프레임의 물리력을 첫 프레임으로 전달하여 루프 애니메이션을 만듭니다"
    )
    
    # RTX 엔진 (주석 처리했던 기능의 백엔드 속성)
    bpy.types.Scene.wiggle_use_gpu = bpy.props.BoolProperty(
        name="Enable RTX Parallel Engine",
        default=False
    )
    
    bpy.types.Scene.wiggle_guide_shape = bpy.props.EnumProperty(
        name="Shape", 
        items=[('BOX', "Box", ""), ('CYLINDER', "Cylinder", ""), ('CAPSULE', "Capsule", "")], 
        default='BOX'
    )

    # 3. 본(PoseBone) 단위 속성 등록
    # [4번 기능] Sim Mix (Influence)
    bpy.types.PoseBone.wiggle_influence = bpy.props.FloatProperty(
        name="Sim Mix", 
        default=1.0, min=0.0, max=1.0, 
        description="애니메이션과 물리 믹스 비율 (0=애니메이션, 1=물리)"
    )

    # [리미트 설정] (AttributeError 해결 핵심)
    bpy.types.PoseBone.wiggle_use_individual_limits = bpy.props.BoolProperty(
        name="Use Individual Limits",
        default=False,
        description="X축(상하)과 Z축(좌우) 리미트를 개별적으로 설정합니다"
    )
    bpy.types.PoseBone.wiggle_angle_limit = bpy.props.FloatProperty(
        name="Angle Limit", default=180.0, min=0.0, max=180.0, precision=1
    )
    bpy.types.PoseBone.wiggle_limit_x = bpy.props.FloatProperty(
        name="X Limit", min=0.0, max=180.0, default=90.0, precision=1
    )
    bpy.types.PoseBone.wiggle_limit_z = bpy.props.FloatProperty(
        name="Z Limit", min=0.0, max=180.0, default=90.0, precision=1
    )

def unregister():
    # 1. 씬 속성 삭제
    if hasattr(bpy.types.Scene, "wiggle_adaptive_damping"): del bpy.types.Scene.wiggle_adaptive_damping
    if hasattr(bpy.types.Scene, "wiggle_safety_threshold"): del bpy.types.Scene.wiggle_safety_threshold
    if hasattr(bpy.types.Scene, "wiggle_use_loop"): del bpy.types.Scene.wiggle_use_loop
    if hasattr(bpy.types.Scene, "wiggle_use_gpu"): del bpy.types.Scene.wiggle_use_gpu
    if hasattr(bpy.types.Scene, "wiggle_guide_shape"): del bpy.types.Scene.wiggle_guide_shape

    # 2. 본 속성 삭제
    if hasattr(bpy.types.PoseBone, "wiggle_influence"): del bpy.types.PoseBone.wiggle_influence
    if hasattr(bpy.types.PoseBone, "wiggle_use_individual_limits"): del bpy.types.PoseBone.wiggle_use_individual_limits
    if hasattr(bpy.types.PoseBone, "wiggle_angle_limit"): del bpy.types.PoseBone.wiggle_angle_limit
    if hasattr(bpy.types.PoseBone, "wiggle_limit_x"): del bpy.types.PoseBone.wiggle_limit_x
    if hasattr(bpy.types.PoseBone, "wiggle_limit_z"): del bpy.types.PoseBone.wiggle_limit_z

    # 3. 클래스 해제 (역순)
    for cls in reversed(classes):
        if hasattr(bpy.types, cls.__name__):
            bpy.utils.unregister_class(cls)
