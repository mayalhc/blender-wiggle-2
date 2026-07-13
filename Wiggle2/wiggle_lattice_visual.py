import bpy
import mathutils
import gpu
import math
from gpu_extras.batch import batch_for_shader

_WIGGLE_RTX_GL_ENGINE = None

class WiggleRTXHorizontalEngine:
    def __init__(self, armature_obj):
        self.obj = armature_obj
        self.tail_pairs = []
        self.stiffness = 0.02
        self.stretch_elasticity = 0.01
        self.damping_to_rest = 0.5
        self.prev_force_dirs = {}
        self.mesh_obj = None
        self.build_name_based_network()
        self.create_lattice_mesh()

    def build_name_based_network(self):
        try:
            pose_bones = self.obj.pose.bones
        except (ReferenceError, AttributeError):
            return
        if not pose_bones:
            return

        # [버그 수정] 전체 본이 아니라 wiggle_tail이 켜진 본만 대상으로 함.
        # 원래 코드는 팔/갈비뼈처럼 같은 깊이(depth)에 있는 전혀 무관한 본까지
        # 짝지어서 evaluate_physics()에서 회전시켜버리는 문제가 있었음.
        wiggle_bones = [pb for pb in pose_bones if getattr(pb, "wiggle_tail", False)]
        if len(wiggle_bones) < 3:
            return

        matrix_map = {}
        for pb in wiggle_bones:
            depth = 0
            parent = pb.parent
            while parent:
                depth += 1
                parent = parent.parent
            if depth not in matrix_map:
                matrix_map[depth] = []
            matrix_map[depth].append(pb)

        for depth, bones in matrix_map.items():
            if len(bones) < 3:
                continue

            # 월드 좌표 기준으로 본들을 X축 정렬 (기본 베이스)
            sorted_bones_base = sorted(bones, key=lambda pb: (self.obj.matrix_world @ pb.head).x)
            num_bones = len(sorted_bones_base)
            is_ring = False

            # [버그 수정]: 리스트 인덱싱 [0] 누락 해결 및 정확한 거리 판별
            if num_bones >= 3:
                first_pos = self.obj.matrix_world @ sorted_bones_base[0].head
                last_pos = self.obj.matrix_world @ sorted_bones_base[-1].head
                edge_dist = (first_pos - last_pos).length

                total_dist = 0
                for i in range(num_bones - 1):
                    p1 = self.obj.matrix_world @ sorted_bones_base[i].head
                    p2 = self.obj.matrix_world @ sorted_bones_base[i + 1].head
                    total_dist += (p1 - p2).length

                avg_dist = total_dist / (num_bones - 1) if num_bones > 1 else 1.0
                if edge_dist < avg_dist * 2.5: # 판정 범위를 살짝 넓혀 링 구조 안정적 포착
                    is_ring = True

            if is_ring:
                # [핵심 개선]: head 대신 본의 head와 tail의 중간 중심점을 활용해 원형의 중심(Center)을 계산
                world_centers = []
                for b in bones:
                    w_head = self.obj.matrix_world @ b.head
                    w_tail = self.obj.matrix_world @ b.tail
                    world_centers.append((w_head + w_tail) * 0.5)

                center_x = sum(co.x for co in world_centers) / len(bones)
                center_y = sum(co.y for co in world_centers) / len(bones)

                # [핵심 개선]: 본의 중간 중심점(Center)을 기준으로 360도 원형 각도를 정밀하게 측정
                def get_horizontal_angle(pb):
                    w_head = self.obj.matrix_world @ pb.head
                    w_tail = self.obj.matrix_world @ pb.tail
                    w_mid = (w_head + w_tail) * 0.5
                    dx = w_mid.x - center_x
                    dy = w_mid.y - center_y
                    return math.atan2(dy, dx)

                # 각도순(-PI ~ PI)으로 본을 완벽하게 원형 정렬
                sorted_ring_bones = sorted(bones, key=get_horizontal_angle)

                # 순서대로 이웃한 본끼리만 링 형태로 체인 연결 (가로지르기 방지)
                for i in range(num_bones):
                    bone_A = sorted_ring_bones[i]
                    bone_B = sorted_ring_bones[(i + 1) % num_bones] # 마지막 본은 다시 첫 번째 본과 연결

                    w_tail_A = self.obj.matrix_world @ bone_A.tail
                    w_tail_B = self.obj.matrix_world @ bone_B.tail
                    tail_dist = (w_tail_A - w_tail_B).length

                    # 원형 구조 특성상 링크 제한 거리를 유연하게 설정 (필요시 5.0보다 크게 조절)
                    if tail_dist < 10.0:
                        self.tail_pairs.append((bone_A.name, bone_B.name, tail_dist))
            else:
                # 평면 일직선 본 구조일 때의 기존 연결 로직
                for i in range(num_bones - 1):
                    bone_A = sorted_bones_base[i]
                    bone_B = sorted_bones_base[i + 1]
                    w_tail_A = self.obj.matrix_world @ bone_A.tail
                    w_tail_B = self.obj.matrix_world @ bone_B.tail
                    tail_dist = (w_tail_A - w_tail_B).length
                    if tail_dist < 5.0:
                        self.tail_pairs.append((bone_A.name, bone_B.name, tail_dist))

    def _mesh_name(self):
        # [버그 수정] 이름이 "Lattice_Mesh"로 고정이라 아마추어가 두 개 이상일 때
        # 서로의 메쉬/엔진 데이터를 덮어썼음. 아마추어 이름을 포함해 유일하게 만듦.
        return f"Lattice_Mesh_{self.obj.name}"

    def create_lattice_mesh(self):
        mesh_name = self._mesh_name()
        existing_obj = bpy.data.objects.get(mesh_name)
        if existing_obj:
            self.mesh_obj = existing_obj
            self.mesh_obj["wiggle_target_armature"] = self.obj.name
            self.update_mesh_vertices()
            return

        if not self.tail_pairs:
            return

        mesh_data = bpy.data.meshes.new(name=f"Wiggle_Lattice_Mesh_Data_{self.obj.name}")
        verts = []
        edges = []

        for i, (name_A, name_B, _) in enumerate(self.tail_pairs):
            verts.append((0.0, 0.0, 0.0))
            verts.append((0.0, 0.0, 0.0))
            edges.append((i * 2, i * 2 + 1))

        mesh_data.from_pydata(verts, edges, [])
        mesh_data.update()

        self.mesh_obj = bpy.data.objects.new(name=mesh_name, object_data=mesh_data)
        self.mesh_obj["wiggle_target_armature"] = self.obj.name
        bpy.context.scene.collection.objects.link(self.mesh_obj)
        self.update_mesh_vertices()

    def update_mesh_vertices(self):
        try:
            if not self.mesh_obj or self.mesh_obj.name not in bpy.data.objects:
                return
            if not self.obj or self.obj.name not in bpy.data.objects:
                return
            pose_bones = self.obj.pose.bones
            mat_world = self.obj.matrix_world
            mesh_data = self.mesh_obj.data
        except ReferenceError:
            return

        if len(mesh_data.vertices) < len(self.tail_pairs) * 2:
            return

        for i, (name_A, name_B, _) in enumerate(self.tail_pairs):
            if name_A not in pose_bones or name_B not in pose_bones:
                continue

            w_tail_A = mat_world @ pose_bones[name_A].tail
            w_tail_B = mat_world @ pose_bones[name_B].tail

            mesh_data.vertices[i * 2].co = w_tail_A
            mesh_data.vertices[i * 2 + 1].co = w_tail_B

    def remove_lattice_mesh(self):
        try:
            old_objs = [o for o in bpy.data.objects if o.name.startswith("Lattice_Mesh")]
            for old_obj in old_objs:
                bpy.data.objects.remove(old_obj, do_unlink=True)
            old_meshes = [m for m in bpy.data.meshes if m.name.startswith("Wiggle_Lattice_Mesh_Data")]
            for old_mesh in old_meshes:
                bpy.data.meshes.remove(old_mesh, do_unlink=True)
        except:
            pass

    def evaluate_physics(self, context, current_stiffness, current_stretch):
        try:
            if not self.obj or self.obj.name not in bpy.data.objects:
                return False
            pose_bones = self.obj.pose.bones
            mat_world = self.obj.matrix_world
        except ReferenceError:
            return False

        self.update_mesh_vertices()

        if not context.screen.is_animation_playing:
            return True

        mat_world_inv = mat_world.inverted()

        # [수정] pb.scale 직접 조작 제거 — wiggle_2 stretch 연산과 충돌하여 본 찌그러짐 발생

        colliders_data = []
        for pb in pose_bones:
            # [버그 수정] wiggle_collider_radius를 이제 모든 본에 전역 등록하기
            # 때문에 hasattr()은 항상 True가 됨 - "or hasattr(...)"이 있으면
            # wiggle_is_collider 토글과 무관하게 모든 본이 콜라이더가 되어버림.
            # 실제 토글 값만으로 판단해야 함.
            if getattr(pb, "wiggle_is_collider", False):
                c_loc = mat_world @ pb.head
                c_rad = getattr(pb, "wiggle_collider_radius", 0.03)
                colliders_data.append((c_loc, c_rad))

        for name_A, name_B, rest_dist in self.tail_pairs:
            if name_A not in pose_bones or name_B not in pose_bones:
                continue
            pb_A = pose_bones[name_A]
            pb_B = pose_bones[name_B]
            w_tail_A = mat_world @ pb_A.tail
            w_tail_B = mat_world @ pb_B.tail
            current_vec = w_tail_B - w_tail_A
            current_dist = current_vec.length
            if current_dist == 0:
                continue
            delta = current_dist - rest_dist
            pair_key = (name_A, name_B)

            # [버그 수정] current_stretch가 받아만 놓고 안 쓰이던 죽은 매개변수였음.
            # 데드존(허용 오차) 크기로 실제 사용 - 값이 클수록 더 늘어나도록 허용.
            tolerance = max(0.0005, current_stretch)
            if abs(delta) < tolerance:
                # [버그 수정] self.damping_to_rest도 설정만 되고 안 쓰이던 값.
                # 보정이 필요 없어진 쌍은 이전 힘을 서서히 감쇠시켜 뚝 끊기지 않게 함.
                if pair_key in self.prev_force_dirs:
                    self.prev_force_dirs[pair_key] = self.prev_force_dirs[pair_key] * (1.0 - self.damping_to_rest)
                continue

            local_force_dir = (mat_world_inv.to_3x3() @ current_vec.normalized()) * delta
            collision_offset = mathutils.Vector((0, 0, 0))
            for c_loc, c_rad in colliders_data:
                to_tail_A = w_tail_A - c_loc
                if to_tail_A.length < c_rad and to_tail_A.length > 0:
                    collision_offset += to_tail_A.normalized() * (c_rad - to_tail_A.length)

                to_tail_B = w_tail_B - c_loc
                if to_tail_B.length < c_rad and to_tail_B.length > 0:
                    collision_offset += to_tail_B.normalized() * (c_rad - to_tail_B.length)

            if collision_offset.length > 0:
                local_col_dir = mat_world_inv.to_3x3() @ collision_offset
                local_force_dir += local_col_dir * 3.0

            # [버그 수정] self.stretch_elasticity/self.prev_force_dirs도 설정만 되고
            # 안 쓰이던 값들 - 이전 프레임 힘 방향과 블렌딩해서 급격한 스냅 방지.
            prev_dir = self.prev_force_dirs.get(pair_key, local_force_dir)
            blend_fac = max(0.0, min(1.0, 1.0 - self.stretch_elasticity))
            local_force_dir = prev_dir.lerp(local_force_dir, blend_fac)
            self.prev_force_dirs[pair_key] = local_force_dir

            bone_local_y = mathutils.Vector((0, 1, 0))
            target_direction = (bone_local_y + local_force_dir * current_stiffness * 0.5).normalized()
            rot_offset = bone_local_y.rotation_difference(target_direction)
            if pb_A.rotation_mode == 'QUATERNION':
                pb_A.rotation_quaternion = pb_A.rotation_quaternion @ rot_offset
            elif pb_A.rotation_mode == 'AXIS_ANGLE':
                # [버그 수정] AXIS_ANGLE 모드는 원래 아예 처리되지 않아 회전이 씹혔음.
                aa = pb_A.rotation_axis_angle
                cur_q = mathutils.Quaternion((aa[1], aa[2], aa[3]), aa[0])
                new_q = cur_q @ rot_offset
                axis, angle = new_q.to_axis_angle()
                pb_A.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
            else:
                pb_A.rotation_euler.rotate(rot_offset)
        return True

def update_lattice_show_debug(self, context):
    if not self.wiggle_lattice_show_debug:
        old_objs = [o for o in bpy.data.objects if o.name.startswith("Lattice_Mesh")]
        for old_obj in old_objs:
            try: old_obj.hide_viewport = True
            except: pass
    else:
        old_objs = [o for o in bpy.data.objects if o.name.startswith("Lattice_Mesh")]
        for old_obj in old_objs:
            try: old_obj.hide_viewport = False
            except: pass

    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

def update_lattice_toggle(self, context):
    if self.wiggle_use_lattice:
        if WiggleRTX_ModalVisualOperator.is_running():
            return
        try:
            bpy.ops.wm.wiggle_rtx_horizontal_run('INVOKE_DEFAULT')
        except:
            pass
    else:
        try:
            old_objs = [o for o in bpy.data.objects if o.name.startswith("Lattice_Mesh")]
            for old_obj in old_objs:
                bpy.data.objects.remove(old_obj, do_unlink=True)
            old_meshes = [m for m in bpy.data.meshes if m.name.startswith("Wiggle_Lattice_Mesh_Data")]
            for old_mesh in old_meshes:
                bpy.data.meshes.remove(old_mesh, do_unlink=True)
        except:
            pass
        global _WIGGLE_RTX_GL_ENGINE
        _WIGGLE_RTX_GL_ENGINE = None

class WiggleRTX_ModalVisualOperator(bpy.types.Operator):
    bl_idname = "wm.wiggle_rtx_horizontal_run"
    bl_label = "Wiggle RTX Horizontal Visual Solver"
    _timer = None
    _is_running = False

    @classmethod
    def is_running(cls):
        return cls._is_running

    def modal(self, context, event):
        if not context.scene.wiggle_use_lattice:
            return self.cancel(context)
        if event.type == 'ESC':
            context.scene.wiggle_use_lattice = False
            return self.cancel(context)
        if event.type == 'TIMER':
            global _WIGGLE_RTX_GL_ENGINE
            if _WIGGLE_RTX_GL_ENGINE:
                try:
                    lattice_stiffness = getattr(context.scene, "wiggle_lattice_stiffness", 0.05)
                    lattice_stretch = getattr(context.scene, "wiggle_lattice_stretch", 0.01)
                    success = _WIGGLE_RTX_GL_ENGINE.evaluate_physics(context, lattice_stiffness, lattice_stretch)
                    if not success:
                        return self.cancel(context)
                except ReferenceError:
                    return self.cancel(context)
        return {'PASS_THROUGH'}

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        if WiggleRTX_ModalVisualOperator._is_running:
            return {'FINISHED'}

        active_obj = None
        # [버그 수정] 메쉬 이름을 "Lattice_Mesh"에서 "Lattice_Mesh_<아마추어명>"으로
        # 바꿨으므로(다중 아마추어 이름 충돌 방지) 정확히 일치가 아니라 접두사로 찾아야 함.
        existing_mesh = next((o for o in bpy.data.objects if o.name.startswith("Lattice_Mesh_")), None)
        if existing_mesh and "wiggle_target_armature" in existing_mesh:
            active_obj = bpy.data.objects.get(existing_mesh["wiggle_target_armature"])

        if not active_obj:
            active_obj = context.view_layer.objects.active

        if not active_obj or active_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "Select an Armature Object first!")
            return {'CANCELLED'}

        global _WIGGLE_RTX_GL_ENGINE
        _WIGGLE_RTX_GL_ENGINE = WiggleRTXHorizontalEngine(active_obj)

        if not context.scene.wiggle_lattice_show_debug:
            if _WIGGLE_RTX_GL_ENGINE.mesh_obj:
                _WIGGLE_RTX_GL_ENGINE.mesh_obj.hide_viewport = True

        wm = context.window_manager
        WiggleRTX_ModalVisualOperator._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        WiggleRTX_ModalVisualOperator._is_running = True
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        global _WIGGLE_RTX_GL_ENGINE
        wm = context.window_manager
        if WiggleRTX_ModalVisualOperator._timer:
            try:
                wm.event_timer_remove(WiggleRTX_ModalVisualOperator._timer)
            except:
                pass
            WiggleRTX_ModalVisualOperator._timer = None

        _WIGGLE_RTX_GL_ENGINE = None
        WiggleRTX_ModalVisualOperator._is_running = False
        return {'CANCELLED'}

@bpy.app.handlers.persistent
def auto_start_lattice_on_load(dummy):
    scene = bpy.context.scene
    if scene and getattr(scene, "wiggle_use_lattice", False):
        try:
            bpy.ops.wm.wiggle_rtx_horizontal_run('INVOKE_DEFAULT')
        except:
            pass

def register():
    try:
        bpy.utils.register_class(WiggleRTX_ModalVisualOperator)
    except ValueError:
        pass
    if auto_start_lattice_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(auto_start_lattice_on_load)

    # [버그 수정] evaluate_physics()가 참조하던 wiggle_is_collider /
    # wiggle_collider_radius가 어디에도 등록돼 있지 않아서 자기충돌 회피
    # 기능이 원천적으로 켜질 수 없었음. 실제 프로퍼티로 등록.
    if not hasattr(bpy.types.PoseBone, "wiggle_is_collider"):
        bpy.types.PoseBone.wiggle_is_collider = bpy.props.BoolProperty(
            name="Lattice Collider",
            description="Nearby lattice pairs push away from this bone's head",
            default=False
        )
    if not hasattr(bpy.types.PoseBone, "wiggle_collider_radius"):
        bpy.types.PoseBone.wiggle_collider_radius = bpy.props.FloatProperty(
            name="Lattice Collider Radius", default=0.03, min=0.0
        )

    # [버그 수정] wiggle_lattice_stretch도 evaluate_physics()에 값은 넘어갔지만
    # Scene 프로퍼티로 등록된 적이 없어 항상 getattr 기본값(0.01)만 쓰였음.
    if not hasattr(bpy.types.Scene, "wiggle_lattice_stretch"):
        bpy.types.Scene.wiggle_lattice_stretch = bpy.props.FloatProperty(
            name="Lattice Stretch Tolerance",
            description="How much a lattice pair can deviate before being pulled back",
            default=0.01, min=0.0, max=1.0
        )

def unregister():
    if auto_start_lattice_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(auto_start_lattice_on_load)
    try:
        bpy.utils.unregister_class(WiggleRTX_ModalVisualOperator)
    except (RuntimeError, ValueError):
        pass

    for attr in ("wiggle_is_collider", "wiggle_collider_radius"):
        if hasattr(bpy.types.PoseBone, attr):
            try: delattr(bpy.types.PoseBone, attr)
            except Exception: pass
    if hasattr(bpy.types.Scene, "wiggle_lattice_stretch"):
        try: delattr(bpy.types.Scene, "wiggle_lattice_stretch")
        except Exception: pass
