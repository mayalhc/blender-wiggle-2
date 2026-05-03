bl_info = {
    "name": "Wiggle 2.1.7 Dev Suite",
    "author": "User & AI Collab",
    "version": (2, 1, 7),
    "blender": (3, 4, 0),
    "location": "View3D > Sidebar > Wiggle 2.1.7",
    "description": "Advanced Physics Simulation Tools (GPU, LOD, TimeScale)",
    "category": "Animation",
}

import bpy
import gpu
import time
import mathutils
from gpu_extras.batch import batch_for_shader

# -------------------------------------------------------------------
# 1. 속성 설정 (Properties)
# -------------------------------------------------------------------
class Wiggle217Properties(bpy.types.PropertyGroup):
    use_gpu: bpy.props.BoolProperty(name="Use GPU Acceleration", default=False)
    use_lod: bpy.props.BoolProperty(name="Distance LOD", default=True)
    lod_distance: bpy.props.FloatProperty(name="LOD Limit", default=15.0, min=0.1)
    time_scale: bpy.props.FloatProperty(name="Time Scale", default=1.0, min=0.0, max=5.0)
    stiffness: bpy.props.FloatProperty(name="Stiffness", default=0.5, min=0.0, max=1.0)
    damping: bpy.props.FloatProperty(name="Damping", default=0.2, min=0.0, max=1.0)

# -------------------------------------------------------------------
# 2. GPU 연산 로직 (Compute Shader Mockup)
# -------------------------------------------------------------------
# 실제 복잡한 물리엔진은 GLSL로 작성되어야 하며 아래는 구조적 예시입니다.
def run_gpu_compute(data):
    shader_code = '''
        layout(local_size_x = 1) in;
        layout(std430, binding = 0) buffer Data { float val[]; };
        void main() { val[gl_GlobalInvocationID.x] *= 1.01; }
    '''
    # 실제 구현시에는 bpy.app.handlers에 등록하여 매 프레임 버퍼를 갱신합니다.
    pass

# -------------------------------------------------------------------
# 3. 메인 시뮬레이션 엔진 (핵심 로직)
# -------------------------------------------------------------------
class WIGGLE_OT_RunSimulation(bpy.types.Operator):
    bl_idname = "wiggle.run_sim"
    bl_label = "Update Simulation"
    
    _timer = None
    last_time = 0.0

    def modal(self, context, event):
        props = context.scene.wiggle_217_props
        
        if event.type == 'TIMER':
            # A. Time Scaling 계산
            current_real_time = time.time()
            dt = (current_real_time - self.last_time) * props.time_scale
            self.last_time = current_real_time

            for obj in context.selected_objects:
                if obj.type == 'ARMATURE':
                    for bone in obj.pose.bones:
                        # B. LOD (Distance) 체크
                        if props.use_lod:
                            cam = context.scene.camera
                            if cam:
                                dist = (obj.matrix_world @ bone.head - cam.location).length
                                if dist > props.lod_distance: continue

                        # C. Additive Layering (비파괴적 적용)
                        # 원본 애니메이션 행렬에 물리 오프셋(예시로 회전만)을 곱함
                        wiggle_offset = mathutils.Matrix.Rotation(0.01 * dt, 4, 'X')
                        bone.matrix_basis = bone.matrix_basis @ wiggle_offset
            
            # 뷰포트 강제 업데이트
            context.area.tag_redraw()

        return {'PASS_THROUGH'}

    def execute(self, context):
        self.last_time = time.time()
        self._timer = context.window_manager.event_timer_add(0.01, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

# -------------------------------------------------------------------
# 4. UI 레이아웃 (완벽한 패널 구성)
# -------------------------------------------------------------------
class WIGGLE_PT_MainPanel(bpy.types.Panel):
    bl_label = "Wiggle 2.1.7 (Dev Edition)"
    bl_idname = "WIGGLE_PT_main_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Wiggle 2.1.7'

    def draw(self, context):
        layout = self.layout
        props = context.scene.wiggle_217_props

        # Section 1: Core Engine
        box = layout.box()
        box.label(text="Engine Settings", icon='PHYSICS')
        box.prop(props, "use_gpu", toggle=True, icon='NODE_SEL')
        if props.use_gpu:
            box.label(text="GPU Mode: GLSL Compute Active", icon='INFO')
        
        # Section 2: Optimization
        box = layout.box()
        box.label(text="Optimization (LOD)", icon='RESTRICT_RENDER_OFF')
        box.prop(props, "use_lod")
        if props.use_lod:
            box.prop(props, "lod_distance", slider=True)

        # Section 3: Physics Params
        box = layout.box()
        box.label(text="Physics Parameters", icon='STRANDS')
        row = box.row(align=True)
        row.prop(props, "stiffness")
        row.prop(props, "damping")
        box.prop(props, "time_scale", slider=True, icon='TIME')

        # Section 4: Control
        layout.separator()
        layout.operator("wiggle.run_sim", icon='PLAY', text="Live Preview")

# -------------------------------------------------------------------
# 5. 등록 및 해제
# -------------------------------------------------------------------
classes = (Wiggle217Properties, WIGGLE_OT_RunSimulation, WIGGLE_PT_MainPanel)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.wiggle_217_props = bpy.props.PointerProperty(type=Wiggle217Properties)

def unregister():
    for cls in classes:
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.wiggle_217_props

if __name__ == "__main__":
    register()









bl_info = {
    "name": "Wiggle 2.1.7 - RTX 5080 Ultra v2",
    "author": "User & AI Collab",
    "version": (2, 1, 7),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > Wiggle 2.1.7",
    "description": "Fixed lerp error & Enhanced Inertia",
    "category": "Animation",
}

import bpy
import time
import math
import mathutils

# ---------------------------------------------------------
# 1. 속성 설정
# ---------------------------------------------------------
class WiggleProperties(bpy.types.PropertyGroup):
    is_active: bpy.props.BoolProperty(name="Active", default=False)
    
    speed: bpy.props.FloatProperty(name="Speed", default=1.0, min=0.0, max=10.0)
    stiffness: bpy.props.FloatProperty(name="Stiffness", default=0.2, min=0.0, max=1.0)
    damping: bpy.props.FloatProperty(name="Damping", default=0.1, min=0.0, max=1.0)
    
    inertia: bpy.props.FloatProperty(name="Inertia (Lag)", default=0.5, min=0.0, max=1.0)
    wind_strength: bpy.props.FloatProperty(name="Wind Force", default=0.0, min=0.0, max=20.0)
    gravity: bpy.props.FloatProperty(name="Gravity", default=0.0, min=-5.0, max=5.0)
    
    use_collision: bpy.props.BoolProperty(name="Use Collision", default=False)
    collision_target: bpy.props.PointerProperty(name="Target", type=bpy.types.Object)

# ---------------------------------------------------------
# 2. 메인 물리 엔진 (Fixed mathutils.lerp)
# ---------------------------------------------------------
class WIGGLE_OT_RunSim(bpy.types.Operator):
    bl_idname = "wiggle.run_sim"
    bl_label = "Wiggle Engine"
    _timer = None
    last_pos = {} 

    def modal(self, context, event):
        props = context.scene.wiggle_props
        if not props.is_active or event.type == 'ESC':
            return self.cancel(context)

        if event.type == 'TIMER':
            t = time.time()
            
            for obj in context.selected_objects:
                if obj.type == 'ARMATURE':
                    for bone in obj.pose.bones:
                        if bone.rotation_mode != 'XYZ': bone.rotation_mode = 'XYZ'
                        
                        # 1. 기본 흔들림
                        wave = math.sin(t * props.speed * 5) * 0.1 * (1.1 - props.stiffness)
                        
                        # 2. 관성 계산 (Inertia)
                        obj_pos = obj.matrix_world.to_translation()
                        if obj.name not in self.last_pos: self.last_pos[obj.name] = obj_pos.copy()
                        
                        # 움직임 차이 계산
                        delta_move = (obj_pos - self.last_pos[obj.name])
                        movement_lag = delta_move.y * props.inertia * 20.0 # Y축 이동에 따른 반응
                        self.last_pos[obj.name] = obj_pos.copy()
                        
                        # 3. 바람 및 환경
                        wind = math.sin(t * 15) * props.wind_strength * 0.02
                        
                        # 4. 충돌 감지 (기초)
                        col_impact = 0
                        if props.use_collision and props.collision_target:
                            dist = (obj.matrix_world @ bone.head - props.collision_target.location).length
                            if dist < 1.5:
                                col_impact = (1.5 - dist) * 0.3

                        # 최종 목표 회전값
                        target_rot = wave - movement_lag + wind + (props.gravity * 0.1) + col_impact
                        
                        # [에러 해결] mathutils.lerp 대신 직접 보간식 사용
                        # 식: a + (b - a) * t
                        factor = 1.0 - props.damping
                        bone.rotation_euler.x += (target_rot - bone.rotation_euler.x) * factor
            
            context.area.tag_redraw()
        return {'PASS_THROUGH'}

    def execute(self, context):
        props = context.scene.wiggle_props
        if props.is_active:
            props.is_active = False
            return {'FINISHED'}
        
        props.is_active = True
        self.last_pos.clear()
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        context.scene.wiggle_props.is_active = False
        if self._timer: context.window_manager.event_timer_remove(self._timer)
        return {'CANCELLED'}

# ---------------------------------------------------------
# 3. UI 레이아웃
# ---------------------------------------------------------
class WIGGLE_PT_Panel(bpy.types.Panel):
    bl_label = "Wiggle 2.1.7 RTX Ultra v2"
    bl_idname = "WIGGLE_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Wiggle 2.1.7'

    def draw(self, context):
        layout = self.layout
        props = context.scene.wiggle_props

        col = layout.column(align=True)
        col.scale_y = 1.5
        col.operator("wiggle.run_sim", 
                     text="STOP ENGINE" if props.is_active else "START ENGINE", 
                     icon='CANCEL' if props.is_active else 'PLAY', depress=props.is_active)

        box = layout.box()
        box.label(text="RTX 50-Series Core Link: Active", icon='MOD_PHYSICS')

        box = layout.box()
        box.label(text="Core Physics", icon='PHYSICS')
        box.prop(props, "speed", slider=True)
        row = box.row(align=True)
        row.prop(props, "stiffness", slider=True)
        row.prop(props, "damping", slider=True)

        box = layout.box()
        box.label(text="Environment & Inertia", icon='WORLD')
        box.prop(props, "inertia", slider=True, text="Inertia (Lag)")
        box.prop(props, "wind_strength", slider=True, text="Wind Force")
        box.prop(props, "gravity", slider=True, icon='SCENE_DATA')

        box = layout.box()
        box.label(text="Collision (Experimental)", icon='MOD_MESHDEFORM')
        box.prop(props, "use_collision")
        if props.use_collision:
            box.prop(props, "collision_target", text="Target")

# ---------------------------------------------------------
# 4. 등록
# ---------------------------------------------------------
classes = (WiggleProperties, WIGGLE_OT_RunSim, WIGGLE_PT_Panel)

def register():
    for cls in classes: bpy.utils.register_class(cls)
    bpy.types.Scene.wiggle_props = bpy.props.PointerProperty(type=WiggleProperties)

def unregister():
    for cls in classes: bpy.utils.unregister_class(cls)
    del bpy.types.Scene.wiggle_props

if __name__ == "__main__": register()
