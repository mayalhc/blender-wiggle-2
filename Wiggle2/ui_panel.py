import bpy
from . import wiggle_layers
from mathutils import Vector, Matrix
from .wiggle_2 import WiggleReset, WiggleToggleBBox, WigglePreset

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

class WIGGLE_PT_Settings(WigglePanel, bpy.types.Panel):
    bl_label = 'Wiggle 2 Physics'
    bl_idname = "WIGGLE_PT_Settings"

    def draw(self, context):
        layout, scene, obj = self.layout, context.scene, context.object
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
            row.prop(obj, 'wiggle_mute', icon='ARMATURE_DATA' if not obj.wiggle_mute else 'HIDE_ON', icon_only=True, invert_checkbox=True, emboss=False)
            row.label(text=f"Object: {obj.name}")

        # Check Active Pose Bone
        pb = context.active_pose_bone
        if pb:
            # 3. Individual Bone Mute
            row.prop(pb, 'wiggle_mute', icon='BONE_DATA' if not pb.wiggle_mute else 'HIDE_ON', icon_only=True, invert_checkbox=True, emboss=False)

            # 4. Limit Settings
            layout.separator()
            main_col = layout.column(align=True)
            
            # Safe registration synchronization for Blender 5.1.2 compatibility
            if hasattr(pb, "id_properties_ensure"):
                try: pb.id_properties_ensure()
                except: pass

            use_ind_limits = getattr(pb, "wiggle_use_individual_limits", False)
            
            # Render main toggle switch safely
            if hasattr(pb, "wiggle_use_individual_limits"):
                icon = 'CHECKMARK' if use_ind_limits else 'RADIOBUT_OFF'
                main_col.prop(pb, "wiggle_use_individual_limits", text="Use Individual Limits", toggle=True, icon=icon)
            else:
                main_col.label(text="Individual Limits property not initialized.", icon='INFO')

            inner_box = main_col.box()
            
            if use_ind_limits:
                col = inner_box.column(align=True)
                if hasattr(pb, "wiggle_limit_x"):
                    col.prop(pb, "wiggle_limit_x", text="X (up and down)")
                if hasattr(pb, "wiggle_limit_z"):
                    col.prop(pb, "wiggle_limit_z", text="Z (right and left)")
            else:
                # Emergency Fallback: If Blender 5.1.2 internal registry lags, show graceful warning instead of crash
                if hasattr(pb, "wiggle_angle_limit"):
                    inner_box.prop(pb, "wiggle_angle_limit", text="Total Limit")
                elif "wiggle_angle_limit" in pb.keys():
                    inner_box.prop(pb, '["wiggle_angle_limit"]', text="Total Limit")
                else:
                    inner_box.label(text="Total Limit property not loaded yet.", icon='INFO')


class WIGGLE_PT_SimMixLayer_v3(bpy.types.Panel):
    bl_label = 'Sim Mix Layers'
    bl_idname = "WIGGLE_PT_SimMixLayer_v3"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Wiggle 2'
    bl_parent_id = "WIGGLE_PT_Settings" 

    def draw(self, context):
        layout = self.layout
        # [수정] 포즈 본이 아닌 활성 오브젝트를 가져옵니다.
        obj = context.active_object
        
        # 오브젝트가 없거나, 리스트 속성이 없는 경우를 대비한 예외 처리
        if not obj:
            layout.label(text="No active object selected")
            return

        # 1. 상단: 레이어 리스트 (Object의 wiggle_layers 참조)
        row = layout.row()
        # [수정] pb 대신 obj를 사용합니다.
        row.template_list("WIGGLE_UL_SimMixLayers", "", obj, "wiggle_layers", obj, "wiggle_layer_index")
        
        col = row.column(align=True)
        col.operator("wiggle.layer_action", icon='ADD', text="").action = 'ADD'
        col.operator("wiggle.layer_action", icon='REMOVE', text="").action = 'REMOVE'
        col.separator()
        col.operator("wiggle.layer_action", icon='TRIA_UP', text="").action = 'UP'
        col.operator("wiggle.layer_action", icon='TRIA_DOWN', text="").action = 'DOWN'

        # 2. 하단 상세 설정 박스
        if hasattr(obj, "wiggle_layers") and len(obj.wiggle_layers) > 0:
            active_layer = obj.wiggle_layers[obj.wiggle_layer_index]
            box = layout.box()
            box.label(text=f"Mix Settings: {active_layer.name}", icon='SETTINGS')
            
            # [입력 1] 레이어 적용 확률
            row = box.row(align=True)
            row.prop(active_layer, "influence", text="Layer Weight (%)", slider=True)
            
            # [입력 2] 해당 레이어의 실제 물리 강도
            row = box.row(align=True)
            row.prop(active_layer, "sim_mix", text="Sim Mix (Physics)", slider=True)
            # 오브젝트 모드용 오퍼레이터로 이름이 동일하다면 유지, 다르다면 확인 필요
            row.operator("wiggle.apply_mix_to_chain", text="", icon='LINKED')
            
            # 레이어 타입 선택
            row = box.row(align=True)
            row.prop(active_layer, "type", expand=True)
            
            # 3. 최종 베이크 버튼
            layout.separator()
            row = layout.row()
            row.scale_y = 1.2
            row.operator("wiggle.bake_combined", icon='RENDER_ANIMATION', text="Bake Result C")
        else:
            layout.label(text="Add a layer to start", icon='INFO')





# ----------------------------------------------------------------
# 이 오퍼레이터는 별도의 파일이나 wiggle_2.py의 register 부분에 있어야 합니다.
# ----------------------------------------------------------------
class WIGGLE_OT_ApplyMixToChain(bpy.types.Operator):
    """활성 Sim Mix 레이어의 Sim Mix 값을 이 오브젝트의 모든 위글 본에 즉시 적용합니다"""
    bl_idname = "wiggle.apply_mix_to_chain"
    bl_label = "Apply Mix to Chain"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.object
        if not obj or not hasattr(obj, "wiggle_layers"):
            return {'CANCELLED'}
        idx = getattr(obj, "wiggle_layer_index", -1)
        if not (0 <= idx < len(obj.wiggle_layers)):
            self.report({'WARNING'}, "No active Sim Mix layer.")
            return {'CANCELLED'}

        # [버그 수정] 이 버튼이 active_layer.sim_mix 슬라이더 바로 옆에 있는데
        # 실제로는 엉뚱하게 pb.wiggle_influence(활성 본의 개별 값)를 자식들에게
        # 복사하고 있었음. 버튼 위치와 일치하도록 레이어의 sim_mix 값을 이
        # 오브젝트의 모든 위글 본에 적용하도록 수정.
        target_val = obj.wiggle_layers[idx].sim_mix
        count = 0
        for pb in obj.pose.bones:
            if getattr(pb, "wiggle_tail", False) or getattr(pb, "wiggle_head", False):
                pb.wiggle_influence = target_val
                count += 1

        if context.area:
            context.area.tag_redraw()
        self.report({'INFO'}, f"Sim Mix {target_val:.2f} applied to {count} bone(s).")
        return {'FINISHED'}



class WIGGLE_PT_Tail(WigglePanel, bpy.types.Panel):
    bl_label = ""
    bl_parent_id = 'WIGGLE_PT_Settings'
    bl_options = {'HEADER_LAYOUT_EXPAND'}
    @classmethod
    def poll(cls, context): return context.scene.wiggle_enable and context.object and context.active_pose_bone
    def draw_header(self, context):
        row = self.layout.row(align=True)
        row.prop(context.active_pose_bone, 'wiggle_tail', text="Tail Settings")
        if hasattr(context.active_pose_bone, 'wiggle_tail_mute'):
            row.prop(context.active_pose_bone, 'wiggle_tail_mute', text="", icon='MUTE_IPO_ON', invert_checkbox=False)
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
        elif b.wiggle_collider_type in {'Sphere', 'Box', 'Cylinder', 'Capsule'}:
            layout.prop_search(b, 'wiggle_collider', context.scene, 'objects', text=' ')
            layout.label(text="Size = the object's own scale (no mesh needed)", icon='INFO')

        for p in ['wiggle_radius', 'wiggle_friction', 'wiggle_bounce', 'wiggle_sticky', 'wiggle_chain']:
            if hasattr(b, p): layout.prop(b, p)

class WIGGLE_PT_Head(WigglePanel, bpy.types.Panel):
    bl_label = ""
    bl_parent_id = 'WIGGLE_PT_Settings'
    bl_options = {'HEADER_LAYOUT_EXPAND'}
    @classmethod
    def poll(cls, context): return context.scene.wiggle_enable and context.object and context.active_pose_bone and not context.active_pose_bone.bone.use_connect
    def draw_header(self, context):
        row = self.layout.row(align=True)
        row.prop(context.active_pose_bone, 'wiggle_head', text="Head Settings")
        if hasattr(context.active_pose_bone, 'wiggle_head_mute'):
            row.prop(context.active_pose_bone, 'wiggle_head_mute', text="", icon='MUTE_IPO_ON')
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
        elif b.wiggle_collider_type_head in {'Sphere', 'Box', 'Cylinder', 'Capsule'}:
            layout.prop_search(b, 'wiggle_collider_head', context.scene, 'objects', text=' ')
            layout.label(text="Size = the object's own scale (no mesh needed)", icon='INFO')

        for p in ['wiggle_radius_head','wiggle_friction_head','wiggle_bounce_head','wiggle_sticky_head', 'wiggle_chain_head']:
            if hasattr(b, p): layout.prop(b, p)

        layout.separator()
        if hasattr(b, 'wiggle_max_offset_head'):
            layout.prop(b, 'wiggle_max_offset_head', text="Max Offset")

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
        
        layout.separator()
        row = layout.row(align=True)

        if hasattr(scene, "wiggle_guide_shape"):
            row.prop(scene, "wiggle_guide_shape", text="")
            guide_shape = scene.wiggle_guide_shape
        else:
            guide_shape = 'BOX'

        g_icon = 'MESH_CUBE'
        if guide_shape == 'CYLINDER':
            g_icon = 'MESH_CYLINDER'
        elif guide_shape == 'CAPSULE':
            g_icon = 'MESH_CAPSULE'

        row.operator("wiggle.toggle_bbox", text="Visual Guide", icon=g_icon)

                
        if hasattr(scene, "wiggle"):
            # [중복 제거] Loop Physics 토글은 Bake 패널의 scene.wiggle_use_loop
            # 하나로 통일됨 (wiggle_post의 리셋 로직에 실제로 연결됨).
            # scene.wiggle.loop 는 미사용 레거시 필드로 남겨둠 (하위호환).
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

        # Loop Physics 설정
        layout.prop(scene, "wiggle_use_loop", text="Loop Physics", icon='LOOP_FORWARDS', toggle=True)
        layout.separator()

        # 기존 베이크 설정들
        layout.prop(w, 'preroll')
        layout.prop(w, 'bake_overwrite')
        
        row = layout.row()
        row.enabled = not w.bake_overwrite
        row.prop(w, 'bake_nla')
        
        # [수정 부분] 기존 wiggle.bake 대신 물리 끄기 로직이 포함된 bake_combined 호출
        if hasattr(bpy.ops.wiggle, 'bake_combined'):
            layout.operator('wiggle.bake_combined', text="Bake (with Auto-Off)", icon='REC')
        elif hasattr(bpy.ops.wiggle, 'bake'):
            layout.operator('wiggle.bake', icon='REC')

        # --- 포인트 캐시 (디스크) ---
        if hasattr(scene, "wiggle_cache_enable"):
            layout.separator()
            box = layout.box()
            box.label(text="Disk Point Cache (for scrubbing)", icon='FILE_CACHE')
            box.prop(scene, "wiggle_cache_dir", text="Directory")
            box.prop(scene, "wiggle_cache_enable", text="Use Cache During Playback")
            row = box.row(align=True)
            row.operator("wiggle.bake_cache", icon='REC')
            row.operator("wiggle.clear_cache", icon='TRASH')


class WIGGLE_PT_Safety(WigglePanel, bpy.types.Panel):
    bl_label = 'Wiggle Safety Guard'
    bl_idname = "WIGGLE_PT_Safety"
    bl_parent_id = "WIGGLE_PT_Settings"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        obj = context.object

        # --- 1. Original Adaptive Safety Guard Block ---
        if not hasattr(scene, "wiggle_adaptive_damping"):
            layout.label(text="Error: Properties not registered.", icon='ERROR')
            return

        box = layout.box()
        box.prop(scene, "wiggle_adaptive_damping", text="Adaptive Safety", icon='CHECKMARK')
        col = box.column()
        col.enabled = scene.wiggle_adaptive_damping
        col.prop(scene, "wiggle_safety_threshold", text="Sensitivity", slider=True)
        if hasattr(scene, "wiggle_safety_rot_threshold"):
            col.prop(scene, "wiggle_safety_rot_threshold", text="Rotation Threshold (deg/s)")
        col.label(text="Auto-damps explosive motion (position AND fast spins).")

        # --- Self Collision (point-based, per-object, off by default) ---
        if obj and hasattr(obj, "wiggle_self_collide"):
            layout.separator()
            box_sc = layout.box()
            box_sc.prop(obj, "wiggle_self_collide", text="Self Collision (this object)", icon='MOD_PHYSICS')
            col_sc = box_sc.column()
            col_sc.enabled = obj.wiggle_self_collide
            col_sc.prop(obj, "wiggle_self_collide_margin", text="Margin")
            col_sc.label(text="Capsule-capsule between wiggle_tail bones (radius = Radius).", icon='INFO')

        # --- 2. Horizontal Lattice Stabilizer ---
        if not hasattr(scene, "wiggle_use_lattice"):
            return

        layout.separator()
        box_lattice = layout.box()
        box_lattice.prop(scene, "wiggle_use_lattice", text="Horizontal Lattice Stabilizer", icon='GRID')

        col_lattice = box_lattice.column(align=True)
        col_lattice.enabled = scene.wiggle_use_lattice
        col_lattice.prop(scene, "wiggle_lattice_stiffness", text="Lattice Stiffness", slider=True)
        if hasattr(scene, "wiggle_lattice_stretch"):
            col_lattice.prop(scene, "wiggle_lattice_stretch", text="Stretch Tolerance", slider=True)
        col_lattice.prop(scene, "wiggle_lattice_show_debug", text="Show Lattice Guide", icon='RESTRICT_VIEW_OFF')
        col_lattice.label(text="Pairs same-depth wiggle bones (skirts, hair bunches).", icon='INFO')

        pb = context.active_pose_bone
        if pb and hasattr(pb, "wiggle_is_collider"):
            row = box_lattice.row(align=True)
            row.prop(pb, "wiggle_is_collider", text="Active Bone is Lattice Collider", toggle=True)
            if pb.wiggle_is_collider:
                row.prop(pb, "wiggle_collider_radius", text="Radius")

# --- REGISTRATION ---

classes = (
    WIGGLE_PT_Settings,          # 메인 부모 패널
    WIGGLE_PT_SimMixLayer_v3,    # [수정] 오브젝트 모드 대응 패널
    WIGGLE_PT_Safety,            # 세이프티 가드 패널
    WIGGLE_PT_Head,              # 헤드 설정 패널
    WIGGLE_PT_Tail,              # 테일 설정 패널
    WIGGLE_PT_Utilities,         # 유틸리티 부모 패널
    WIGGLE_PT_Bake,              # 베이크 패널
    
    WIGGLE_OT_ApplyMixToChain,    # 오퍼레이터
)

def register():
    # 1. 클래스 등록
    for cls in classes:
        try:
            if not hasattr(bpy.types, cls.bl_idname if hasattr(cls, 'bl_idname') else cls.__name__):
                bpy.utils.register_class(cls)
        except RuntimeError as e:
            print(f"Wiggle 2 Registration Error ({cls.__name__}): {e}")

    # 2. 씬(Scene) 단위 속성 등록
    bpy.types.Scene.wiggle_adaptive_damping = bpy.props.BoolProperty(
        name="Safety Guard", default=True, 
        description="Automatic Bone Pop Prevention"
    )
    bpy.types.Scene.wiggle_safety_threshold = bpy.props.FloatProperty(
        name="Sensitivity", default=1.0, min=0.1, max=10.0,
        description="Defense Trigger Sensitivity"
    )
    bpy.types.Scene.wiggle_safety_rot_threshold = bpy.props.FloatProperty(
        name="Rotation Threshold", default=180.0, min=10.0, max=1080.0,
        description="Object rotation speed (degrees/second) that triggers extra damping - "
                    "catches fast spins/whips that pure position tracking misses"
    )
    bpy.types.Scene.wiggle_use_loop = bpy.props.BoolProperty(
        name="Loop Physics", default=False,
        description="Transfer physics from the last frame to the first to create a loop"
    )
    bpy.types.Scene.wiggle_guide_shape = bpy.props.EnumProperty(
        name="Shape", 
        items=[('BOX', "Box", ""), ('CYLINDER', "Cylinder", ""), ('CAPSULE', "Capsule", "")], 
        default='BOX'
    )

    # 3. 본(PoseBone) 단위 속성 등록
    # wiggle_influence 는 wiggle_layers.py 에서 등록 (default=1.0). 중복 등록 금지.
    bpy.types.PoseBone.wiggle_use_individual_limits = bpy.props.BoolProperty(
        name="Use Individual Limits", default=False,
        description="Set X/Z limits separately. (Alt + Click to sync.)"
    )
    bpy.types.PoseBone.wiggle_angle_limit = bpy.props.FloatProperty(
        name="Angle Limit", default=180.0, min=0.0, max=180.0, precision=1,
        description="Press Alt + Enter to apply change to the entire skeleton"
    )
    bpy.types.PoseBone.wiggle_limit_x = bpy.props.FloatProperty(
        name="X Limit", min=0.0, max=180.0, default=90.0, precision=1
    )
    bpy.types.PoseBone.wiggle_limit_z = bpy.props.FloatProperty(
        name="Z Limit", min=0.0, max=180.0, default=90.0, precision=1
    )
    bpy.types.PoseBone.wiggle_max_offset_head = bpy.props.FloatProperty(
        name="Max Offset", default=0.0, min=0.0,
        description="Clamp how far the floating head may drift from its animated rest position (0 = unlimited)"
    )

def unregister():
    # 1. 클래스 해제
    for cls in reversed(classes):
        if hasattr(bpy.types, cls.bl_idname if hasattr(cls, 'bl_idname') else cls.__name__):
            bpy.utils.unregister_class(cls)

    # 2. 씬 속성 삭제
    props_scene = [
        "wiggle_adaptive_damping", "wiggle_safety_threshold", "wiggle_safety_rot_threshold",
        "wiggle_use_loop", "wiggle_guide_shape"
    ]
    for p in props_scene:
        if hasattr(bpy.types.Scene, p): delattr(bpy.types.Scene, p)

    # 3. 본 속성 삭제
    props_bone = [
        "wiggle_use_individual_limits",
        "wiggle_angle_limit", "wiggle_limit_x", "wiggle_limit_z", "wiggle_max_offset_head"
    ]
    for p in props_bone:
        if hasattr(bpy.types.PoseBone, p): delattr(bpy.types.PoseBone, p)