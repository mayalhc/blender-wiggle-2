import bpy
from bpy.app.handlers import persistent

# ============================================================
# wiggle_layers.py  ─  Sim Mix Layer 시스템  (v5)
#
# 레이어를 추가하는 즉시 실제 액션 + 전용 NLA 트랙을 만들어 연결한다
# (Base 레이어는 "WGL_Base", 각 Sim 레이어는 "WGL_Trk_<이름>"). Layer
# Weight 슬라이더는 그 NLA 스트립들의 influence를 실제로 교차 감쇠
# (cross-fade)하고, Sim Mix는 그와 별개로 실시간 물리 강도
# (pb.wiggle_influence)를 제어한다.
#
# ▣ 이전에 고쳤던(그리고 다시 되풀이하면 안 되는) 버그들
# [Bug-1(재발견)] "정적 influence는 use_animated_influence를 꺼야 먹힌다"
#         고 생각했었는데 완전히 반대였다. 실측(여러 Blender 버전:
#         4.2 LTS/5.0/5.1/5.2에서 전부 동일) 결과, use_animated_influence
#         가 꺼진 정적 상태에서는 strip.influence 값 자체를 평가에서
#         아예 무시한다(0을 줘도 1을 줘도 100% 적용됨) - mute만 즉시
#         반영됨. influence가 실제로 작동하려면 반대로
#         use_animated_influence를 켜고, 그 값으로 CONSTANT 보간
#         키프레임 1개를 넣어야 한다. set_strip_influence()가 이 방식.
# [Bug-2] sync_layers()에서 pb.wiggle_stiff = (1-influence)*80.0 로 매
#         프레임 모든 본의 stiffness를 강제 덮어쓰던 코드 - 사용자가
#         설정한 Stiff(프리셋 포함)를 파괴함. 다시 넣지 말 것.
# [Bug-3] physics_strength 계산에 Layer Weight(active_layer.influence)를
#         곱하지 않아서, Weight를 0으로 내려도 실시간 물리가 안 죽었음.
# [Bug-4] wiggle_influence 기본값을 0.0으로 등록 - 방금 위글을 켠 본은
#         sync가 한 번 돌기 전까지 물리가 아예 안 먹음. 반드시 1.0.
# [Bug-5] 베이크 오퍼레이터가 존재하지도 않는 scene.wiggle.wiggle_mute를
#         대입해서 베이크 버튼을 누르면 바로 AttributeError가 남.
# ============================================================


def set_strip_influence(strip, value):
    """NLA 스트립의 influence를 실제로 적용되게 설정한다. 정적(비-애니메이션)
    influence는 Blender 평가에서 무시되는 것으로 실측 확인됐으므로,
    use_animated_influence를 켜고 그 값으로 CONSTANT 키프레임 1개를 넣는
    방식으로 우회한다(값이 스트립 전체 구간에 고정으로 적용됨)."""
    strip.use_animated_influence = True
    fc = None
    for existing in strip.fcurves:
        if existing.data_path == 'influence':
            fc = existing
            break
    if fc is None:
        fc = strip.fcurves.new('influence')
    for kp in list(fc.keyframe_points):
        fc.keyframe_points.remove(kp)
    kp = fc.keyframe_points.insert(strip.frame_start, value)
    kp.interpolation = 'CONSTANT'
    fc.update()


def _clear_action_keyframes(action):
    """[버그 수정] Blender 5.x부터 Action이 "레이어드 액션"(Action Slots)
    구조로 바뀌면서 action.fcurves가 더 이상 존재하지 않는다(fcurve는
    action.layers[i].strips[j].channelbags[k].fcurves 아래에 있음). 옛
    action.fcurves API를 그대로 쓰면 AttributeError로 베이크가 죽는다.
    두 버전 다 지원하도록 존재하는 쪽을 찾아서 전부 지운다."""
    if hasattr(action, "fcurves"):
        for fc in list(action.fcurves):
            action.fcurves.remove(fc)
        return
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for cb in getattr(strip, "channelbags", []):
                for fc in list(cb.fcurves):
                    cb.fcurves.remove(fc)


def _sync_strip_range(track, action):
    """[버그 수정] Sim 레이어는 처음에 빈 액션으로 만들어지는데, 그 순간
    NLA 스트립의 재생 길이가 액션의 (당시) frame_range로 고정돼버린다
    (실측: action_frame_end가 1.0으로 굳어짐). 나중에 베이크 등으로 그
    액션에 진짜 키프레임이 들어가도(예: 1~20프레임) 스트립은 여전히
    Base 쪽 값만 내보내고 Sim 쪽은 전혀 반영이 안 됐다.

    action_frame_start/end 같은 속성만 고쳐서는 전혀 해결이 안 됨을
    실측으로 확인함 - Blender NLA 스트립은 내부적으로 뭔가 캐시를 들고
    있어서 속성 패치만으로는 재평가에 반영되지 않는다. 확실히 반영되는
    유일한 방법은 스트립을 통째로 지우고 다시 만드는 것이었다. 그래서
    range가 어긋나 있으면 스트립 자체를 새로 만든다(blend_type/influence/
    extrapolation은 호출부에서 이어서 다시 설정함)."""
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
    influence = strip.influence
    track.strips.remove(strip)
    new_strip = track.strips.new(action.name, int(a_start), action)
    new_strip.blend_type = blend_type
    new_strip.extrapolation = 'HOLD'
    set_strip_influence(new_strip, influence)
    return new_strip


def _find_track_for_action(obj, action):
    """이 액션을 담고 있는 (WGL_ 접두사가 아닌 것도 포함) 첫 NLA 트랙을
    찾는다."""
    if not action or not obj.animation_data:
        return None
    for track in obj.animation_data.nla_tracks:
        for strip in track.strips:
            if strip.action == action:
                return track
    return None


def ensure_layer_linked(obj, layer, is_first):
    """레이어에 실제 액션과 전용 NLA 트랙이 연결돼 있는지 확인하고, 없으면
    즉시 만든다. 레이어를 추가한 순간부터 NLA 에디터에서 바로 보이게
    하는 것이 목적."""
    if not obj.animation_data:
        obj.animation_data_create()

    action = bpy.data.actions.get(layer.action_name) if layer.action_name else None
    track = _find_track_for_action(obj, action) if action else None

    if is_first:
        # Base 레이어 - 사용자가 이미 갖고 있는 애니메이션에 연결한다.
        # [버그 수정] 예전에는 layer.action_name이 완전히 비어있을 때
        # "딱 한 번만" 오브젝트의 기존 애니메이션을 찾아 연결했음. 그래서
        # 여러 번 테스트하는 동안 잘못(빈 액션 등) 연결된 상태로 남아있으면
        # 그 뒤로는 다시 확인을 안 해서, 오브젝트에 실제로 있는
        # NLA 트랙(예: "Sections")을 계속 놓치고 있었음. 이제 매 sync마다
        # 오브젝트의 실제 상태(WGL_ 로 시작하지 않는 트랙, 또는 활성 액션)를
        # 다시 확인해서, 지금 연결된 것과 다르면 그 진짜 액션으로 갈아탄다.
        candidate_action, candidate_track = None, None
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
            # [버그 수정] 여기도 "is not"(정체성 비교) 대신 이름 비교를
            # 써야 한다 - Blender 트랙 래퍼 객체는 접근할 때마다 다른
            # Python 인스턴스일 수 있음.
            if candidate_track and (not existing_base or candidate_track.name != existing_base.name):
                # 후보가 별도 트랙(예: "Sections")에 있었던 경우 - 중복
                # 트랙을 만들지 않도록 그 트랙 자체를 WGL_Base로 흡수한다.
                if existing_base:
                    obj.animation_data.nla_tracks.remove(existing_base)
                candidate_track.name = "WGL_Base"
                track = candidate_track
                for s in track.strips:
                    s.blend_type = 'REPLACE'
                    s.extrapolation = 'HOLD'
                    set_strip_influence(s, layer.influence)
            elif existing_base:
                track = existing_base
                for s in track.strips:
                    s.action = action
                    set_strip_influence(s, layer.influence)
            else:
                track = None
            # [완전히 제거] 여기 있던 "활성 액션을 비우는" 코드가 - 딱
            # 한 번만 비우도록 조건을 좁혀놨어도 - 사용자가 Base 애니메이션
            # 액션을 Dope Sheet에서 선택해 재생할 때마다 여전히 반복
            # 트리거돼서 선택이 사라지는 문제를 냈다. 활성 액션은 이제
            # 어떤 경우에도 이 함수에서 건드리지 않는다.

        if not action:
            # 완전히 새 빈 액션이라 활성 액션으로 띄울 필요가 없음(어차피
            # 아래에서 바로 WGL_Base에 연결됨) - 활성 액션으로 만들면
            # NLA 스택과 중복 적용되므로 만들지 않는다.
            action = bpy.data.actions.new("Base_Action")
            layer.action_name = action.name
            track = None

        if not track:
            track = next((t for t in obj.animation_data.nla_tracks if t.name == "WGL_Base"), None)
            if not track:
                track = obj.animation_data.nla_tracks.new()
                track.name = "WGL_Base"
            start_fr = bpy.context.scene.frame_start if bpy.context.scene else 1
            strip = track.strips.new(action.name, int(start_fr), action)
            strip.blend_type = 'REPLACE'
            strip.extrapolation = 'HOLD'
            set_strip_influence(strip, layer.influence)

        track.name = "WGL_Base"
    else:
        # Sim 레이어 - 아직 액션이 없으면 빈 액션을 새로 만들어 연결한다
        # (베이크하면 여기에 실제 키프레임이 들어감).
        if not action:
            action = bpy.data.actions.new(name=f"Act_Sim_{layer.name}")
            layer.action_name = action.name
            track = None

        if not track:
            # [버그 수정] 트랙을 f"WGL_Trk_{layer.name}"이라는, 매번
            # 바뀔 수 있는 이름으로 찾고 있었음(레이어 이름은 액션 이름과
            # 같아지도록 자동으로 바뀜 - 바로 아래 코드). 그래서 이름이
            # 바뀐 다음 sync에서는 기존 트랙을 못 찾고 새 트랙을 하나 더
            # 만들어버렸고(예: "WGL_Trk_Sim_Layer_1"과
            # "WGL_Trk_Act_Sim_Sim_Layer_1" 둘 다 존재), 관리 안 되는
            # 옛 트랙이 그대로 남아 화면에 간섭했다. 이름이 아니라 이미 위
            # (_find_track_for_action)에서 액션 자체로 찾은 track을 그대로
            # 쓰고, 정말 하나도 없을 때만 새로 만든다. 트랙 이름도 액션
            # 이름 기준(안정적)으로 짓는다.
            track_name = f"WGL_Trk_{action.name}".replace(" ", "_")
            track = obj.animation_data.nla_tracks.new()
            track.name = track_name
            strip = next((s for s in track.strips), None)
            if not strip:
                start_fr = bpy.context.scene.frame_start if bpy.context.scene else 1
                strip = track.strips.new(action.name, int(start_fr), action)
                # [버그 수정] COMBINE은 아래 스택(Base) 위에 이 스트립의
                # "델타"를 influence만큼 더하는 방식이라, 델타가 크면
                # influence가 조금만 올라가도 결과가 확 튀어 보임(자연스러운
                # 크로스페이드가 아님). REPLACE로 바꾸면 influence만큼
                # 아래 스택 결과와 이 스트립을 정확히 선형 블렌드해서
                # Weight 0~1이 Base↔Sim 사이를 자연스럽게 섞는다.
                strip.blend_type = 'REPLACE'
                strip.extrapolation = 'HOLD'
                set_strip_influence(strip, layer.influence)

    if not layer.name or layer.name.startswith("Layer") or layer.name.startswith("Sim Layer"):
        layer.name = action.name
    return action, track


def sync_layers(obj):
    """모든 레이어가 실제 액션/트랙에 연결돼 있는지 보장하고, Layer
    Weight를 NLA 스트립 influence로, Sim Mix를 실시간 물리 강도로 각각
    독립적으로 적용한다."""
    if not obj or not hasattr(obj, "wiggle_layers"):
        return
    layers = obj.wiggle_layers
    if not layers:
        return

    # [되돌림] 활성 액션(obj.animation_data.action)이 레이어 액션과
    # 겹치면 자동으로 action_influence를 0으로 낮추던 코드를 제거했다.
    # (참고: Blender는 NLA 트랙이 있어도 활성 액션이 설정돼 있으면 그
    # 위에 추가로 적용한다 - 사용자가 Dope Sheet/Action Editor에서 레이어
    # 액션을 직접 활성 액션으로 남겨두면 이중 적용처럼 보일 수 있다는
    # 알려진 제약. 하지만 이걸 자동으로 고치려는 시도가 매번 새로운
    # 부작용(재생 중 선택 사라짐 → 베이크 시 키프레임 삽입 거부 등)을
    # 만들어서, 더 단순하고 예측 가능한 쪽을 택해 손대지 않기로 함.)

    # 목록에 Base 타입이 하나도 없으면(예: 예전 데이터) 첫 레이어를 승격.
    if not any(l.type == 'BASE' for l in layers):
        layers[0].type = 'BASE'

    if not obj.animation_data:
        obj.animation_data_create()

    # [실측으로 검증] Blender NLA 트랙 스택은 "나중에 만들어진(=
    # nla_tracks 컬렉션에서 인덱스가 높은) 트랙이 이긴다" - 두 트랙에
    # 서로 다른 값을 넣고 실제 평가해서 직접 확인함. 그러므로 Base를
    # 먼저 만들어 낮은 인덱스(기초)에 두고, Sim 레이어들을 그 뒤에 만들어
    # 높은 인덱스(위에서 크로스페이드)에 두는 게 맞다 - 원래 순서 그대로.
    # [버그 수정] 아래 자동 연결/중복 정리/순서 재배치 로직들은 파일마다
    # 구조가 제각각이라 예상 못 한 예외를 던질 수 있는데, 그러면 이 함수가
    # 중간에 멈춰서 정작 제일 중요한 "Layer Weight를 실제로 반영하는"
    # 마지막 단계(맨 아래, blend_type 강제/influence 갱신)까지 절대 못
    # 간다 - 그러면 Weight를 아무리 움직여도 화면이 하나도 안 바뀐다.
    # 각 단계를 개별적으로 방어해서, 한 단계가 실패해도 나머지는 계속
    # 진행되고 마지막 핵심 단계는 반드시 실행되게 한다.
    base_layer_first = next((l for l in layers if l.type == 'BASE'), None)
    try:
        if base_layer_first:
            ensure_layer_linked(obj, base_layer_first, is_first=True)
    except Exception as e:
        print(f"Wiggle2: Base 레이어 연결 실패({e})")
    for layer in layers:
        if layer.type != 'BASE':
            try:
                ensure_layer_linked(obj, layer, is_first=False)
            except Exception as e:
                print(f"Wiggle2: '{layer.name}' 레이어 연결 실패({e})")

    # [버그 수정] 예전엔 트랙을 이름(레이어 이름 기반, 바뀔 수 있음)으로
    # 찾다가 못 찾으면 새로 만들어서, 같은 액션을 가리키는 중복 트랙이
    # 파일에 남아있을 수 있다(예: "WGL_Trk_Sim_Layer_1"과
    # "WGL_Trk_Act_Sim_Sim_Layer_1"가 둘 다 존재). 관리 안 되는 중복
    # 트랙은 뮤트 여부와 무관하게 NLA 스택에 그대로 끼어들어 Layer
    # Weight를 아무리 조정해도 이상하게 섞이는 것처럼 보이게 만든다.
    # 각 레이어의 액션마다 트랙이 정확히 하나만 남도록 정리한다.
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
                # [버그 수정] "t is not keeper"(정체성 비교)를 썼었는데,
                # Blender는 같은 트랙이라도 접근할 때마다 다른 Python 래퍼
                # 객체를 돌려줄 수 있어서 keeper 자기 자신까지 "다른 객체"로
                # 오인해 지워버렸다(중복 정리가 트랙을 전부 삭제해버림). 이름
                # 비교로 바꾼다.
                keeper = _find_track_for_action(obj, act)
                for t in dup_tracks:
                    if t.name != keeper.name:
                        obj.animation_data.nla_tracks.remove(t)
    except Exception as e:
        print(f"Wiggle2: 중복 트랙 정리 실패({e})")

    # 이미 잘못 만들어진 파일(Base가 Sim보다 나중 인덱스=더 강하게 적용됨)
    # 은 Base 트랙을 지우고 맨 앞(컬렉션 인덱스 0)에 다시 만들어서 바로
    # 잡는다. nla_tracks 컬렉션 자체에는 "맨 앞에 삽입"하는 API가 없어서,
    # Base를 지운 뒤 다시 만들면 다른 트랙들 뒤(더 높은 인덱스)에 붙는
    # 문제가 있다 - 그래서 아예 모든 트랙을 한 번에 지우고 Base부터
    # 순서대로 다시 만든다.
    try:
        if base_layer_first and base_layer_first.action_name:
            base_act = bpy.data.actions.get(base_layer_first.action_name)
            track_list = list(obj.animation_data.nla_tracks)
            base_track = next((t for t in track_list if any(s.action == base_act for s in t.strips)), None)
            if base_act and base_track and track_list.index(base_track) != 0:
                saved = []  # (name, action, blend_type, influence, mute)
                for t in track_list:
                    for s in t.strips:
                        saved.append((t.name, s.action, s.blend_type, s.influence, t.mute))
                        break
                for t in track_list:
                    obj.animation_data.nla_tracks.remove(t)
                saved.sort(key=lambda x: 0 if x[1] == base_act else 1)
                start_fr = bpy.context.scene.frame_start if bpy.context.scene else 1
                for name, act, blend_type, influence, mute in saved:
                    if not act:
                        continue
                    nt = obj.animation_data.nla_tracks.new()
                    nt.name = name
                    nt.mute = mute
                    ns = nt.strips.new(act.name, int(start_fr), act)
                    ns.blend_type = blend_type
                    ns.extrapolation = 'HOLD'
                    set_strip_influence(ns, influence)
    except Exception as e:
        print(f"Wiggle2: 트랙 순서 정리 실패({e})")

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

    # [버그 수정] 리스트에서 어떤 레이어를 "선택"했는지는 편집 UI용일 뿐,
    # 다른 레이어를 끄는 기준이 되면 안 됨. 이전에는 선택된 레이어 하나만
    # Base와 교차 감쇠하고 나머지 Sim 레이어는 자기 Weight가 0이 아니어도
    # 무조건 뮤트해버렸음. 이제 Base도 Sim 레이어와 똑같이 자기 자신의
    # Weight/Mute만으로 켜지고 꺼진다(Weight 0이면 Base도 꺼짐).
    # [버그 수정] 뮤트되면(Weight<=0) 스트립의 influence 값 자체는 갱신을
    # 안 하고 트랙만 뮤트해서, NLA 패널엔 예전 값(예: 1.000)이 그대로
    # 남아 Layer Weight=0인데 Influence는 1.000으로 보이는 혼란이 있었다.
    # 뮤트 여부와 무관하게 항상 Influence를 Layer Weight와 일치시킨다
    # (뮤트되면 어차피 트랙 자체가 평가에서 빠지니 값은 안전).
    # blend_type도 매번 강제로 REPLACE로 맞춘다 - 예전(Combine) 코드로
    # 만들어진 스트립이 남아있으면 자연스러운 크로스페이드가 안 됨.
    try:
        base_act, base_track = _act_track(base_layer) if base_layer else (None, None)
        if base_track:
            _sync_strip_range(base_track, base_act)
            base_track.mute = base_layer.mute or base_layer.influence <= 0.0001
            for s in base_track.strips:
                s.blend_type = 'REPLACE'
                set_strip_influence(s, base_layer.influence)
    except Exception as e:
        print(f"Wiggle2: Base 레이어 Weight 적용 실패({e})")

    for layer in layers:
        if layer.type != 'SIM':
            continue
        try:
            act, track = _act_track(layer)
            if not track:
                continue
            _sync_strip_range(track, act)
            track.mute = layer.mute or layer.influence <= 0.0001
            for s in track.strips:
                s.blend_type = 'REPLACE'
                set_strip_influence(s, layer.influence)
        except Exception as e:
            print(f"Wiggle2: '{layer.name}' Weight 적용 실패({e})")

    # 실시간 물리 강도: 활성화된(mute 아닌) 모든 Sim 레이어의
    # (Layer Weight × Sim Mix)를 합산. wiggle_stiff/damp 등 사용자
    # 설정값은 절대 건드리지 않는다.
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
    # [버그 수정] 베이크 오퍼레이터가 자기 프레임 루프에서 scene.frame_set()을
    # 부를 때마다 이 핸들러가 매 프레임 끼어들어 sync_layers()를 실행하면서
    # 베이크용으로 잠깐 설정해둔 활성 액션을 건드려버렸음(방어 코드가 "이미
    # 레이어에 연결된 액션이니 비워도 된다"고 판단해 비움 -> keyframe_insert가
    # 액션 없이 실행되며 Blender가 임시 액션을 자동 생성 -> Base 자동 감지
    # 로직이 그 임시 액션을 진짜 Base로 잘못 채감). 베이크 도중에는 이 핸들러
    # 를 완전히 건너뛴다.
    if _baking_in_progress:
        return
    obj = bpy.context.object
    if obj and obj.type == 'ARMATURE' and hasattr(obj, "wiggle_layers") and obj.wiggle_layers:
        sync_layers(obj)


def update_layer_params(self, context):
    """Layer Weight / Sim Mix / Mute 슬라이더를 움직였을 때 즉시 반영."""
    obj = context.object
    if obj:
        sync_layers(obj)
        # [버그 수정] set_strip_influence()가 NLA 스트립의 influence
        # F-curve 키프레임 값을 바꾸는데, 뷰포트가 그 변경을 즉시
        # 재평가하도록 강제하는 호출이 없으면 프레임이 실제로 바뀌기
        # 전까지 반영이 안 된다(슬라이더를 움직여도 화면이 그대로).
        try:
            context.view_layer.update()
        except Exception:
            pass
        try:
            from . import wiggle_2
            # 실제 물리 계산은 "프레임이 바뀔 때"만 도는 frame_change_post
            # 핸들러라서, 슬라이더만 움직이고 타임라인을 안 건드리면 실시간
            # 물리 미리보기가 안 바뀐다. 물리를 다시 계산하지 않고, 마지막
            # 실제 프레임에서 캐시해둔 애니메이션/시뮬레이션 포즈를 새
            # wiggle_influence로 즉시 재블렌드만 한다(리셋 부작용 없음).
            wiggle_2.build_list()
            wiggle_2.refresh_influence_blend(obj)
        except Exception:
            pass
    if context.screen:
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def update_layer_selection(self, context):
    """[되돌림] 레이어 선택 시 그 액션을 활성 액션으로 띄우는 기능을
    넣었었는데, 그 액션이 이미 뮤트 안 된 NLA 스트립(WGL_Trk_*)에도
    연결돼 있어서 Blender가 NLA 스택 + 활성 액션을 동시에 평가해 같은
    포즈가 두 번 겹쳐 적용되는 문제가 생겼다(NLA 에디터에 액션이 위아래
    두 곳에 나타나 혼란을 준 것도 같은 원인). 액션을 안전하게 편집하려면
    NLA 에디터에서 해당 스트립을 더블클릭해 Tweak Mode로 들어가야 한다
    (그러면 Blender가 그 스트립을 트랙 평가에서 자동으로 빼고 편집 중인
    액션만 단독으로 보여줌). 그래서 여기서는 sync만 하고 활성 액션은
    건드리지 않는다."""
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
                # 첫 번째로 추가하는 Sim 레이어는 Weight 100%(기본값
                # 1.0)로 시작한다.
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


class WIGGLE_OT_BakeCombined(bpy.types.Operator):
    """활성 Sim 레이어의 실시간 블렌드 결과를, 그 레이어에 이미 연결된
    액션에 그대로 굽는다. Bake 패널의 Preroll/Overwrite/NLA 설정을 반영."""
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
        w = scene.wiggle

        if obj.mode != 'POSE':
            bpy.ops.object.mode_set(mode='POSE')
        if not obj.animation_data:
            obj.animation_data_create()

        # [수정] 기존에 선택된 Sim 레이어의 액션에 그대로 덮어 구우면, 그
        # 레이어가 실시간 물리로 계속 새 키프레임을 받는 액션(Act_Sim_*)일
        # 경우 베이크 결과와 충돌해서 덮어써진다. 항상 새 레이어 + 새
        # 액션을 만들어 그쪽에 굽고, Sim Mix Layers 목록에 추가한다. Base는
        # 당연히 대상이 아니다.
        layers = obj.wiggle_layers
        existing_names = {l.name for l in layers}
        n = 1
        new_name = "Bake"
        while new_name in existing_names or bpy.data.actions.get(f"Act_Sim_{new_name}"):
            n += 1
            new_name = f"Bake {n}"

        # [버그 수정] influence/sim_mix는 update=update_layer_params라서
        # 대입하는 순간 바로 sync_layers()가 실행된다. action_name을
        # 설정하기 전에 이 값들부터 대입하면, sync_layers 안의
        # ensure_layer_linked가 "액션이 아직 없다"고 보고 빈 액션을 먼저
        # 하나 만들어버리고, 그 다음 줄에서 또 새 액션을 만들어 액션이
        # 두 개(예: Act_Sim_Bake, Act_Sim_Bake.001) 생기는 원인이었다.
        # 액션을 먼저 만들어 연결까지 끝낸 뒤 마지막에 influence/sim_mix를
        # 설정해야 sync_layers가 이미 있는 액션을 그대로 찾아 쓴다.
        active_layer = layers.add()
        active_layer.type = 'SIM'
        active_layer.name = new_name

        target_action = bpy.data.actions.new(name=f"Act_Sim_{active_layer.name}")
        active_layer.action_name = target_action.name
        obj.wiggle_layer_index = len(layers) - 1
        # 베이크 전에 트랙/스트립을 미리 만들어둬야 아래에서 뮤트할 수 있다.
        ensure_layer_linked(obj, active_layer, is_first=False)

        active_layer.influence = 1.0
        # 베이크 결과는 이미 확정된 키프레임이라, 그 위에 실시간 물리를
        # 또 섞으면 이중 적용된다. 이 레이어는 Sim Mix 0으로 시작해서
        # 구운 키프레임만 그대로 재생하게 한다.
        active_layer.sim_mix = 0.0

        if getattr(w, "bake_nla", False) and obj.animation_data.action:
            old_action = obj.animation_data.action
            track = obj.animation_data.nla_tracks.new()
            track.name = "WGL_PrevAction"
            track.strips.new(old_action.name, int(old_action.frame_range[0]), old_action)

        # [버그 수정] "Due to the NLA stack setup, N keyframe(s) have not
        # been inserted." target_action이 이미 이 레이어의 NLA 스트립에
        # 연결돼 있는 상태에서 그 액션에 직접 키프레임을 넣으려 하면,
        # Blender가 "그 스트립이 COMBINE + 부분 influence로 블렌드되고
        # 있어서 여기 넣는 키프레임이 의도한 결과를 안 만든다"고 판단해
        # 삽입 자체를 거부함. 베이크하는 동안만 그 스트립이 들어있는
        # 트랙을 뮤트해서(=NLA 스택에서 잠깐 빼서) 충돌을 없앤다. 다른
        # 트랙(Base 등)은 그대로 둬서 시각적 참조는 안 바뀜.
        target_track = _find_track_for_action(obj, target_action)
        target_track_prev_mute = target_track.mute if target_track else None
        if target_track:
            target_track.mute = True

        obj.animation_data.action = target_action
        # [안전장치] action_influence/action_blend_type이 1.0/REPLACE가
        # 아니면 "Due to the NLA stack setup, N keyframe(s) have not been
        # inserted." 경고와 함께 베이크가 조용히 실패할 수 있다. 베이크는
        # 이 액션을 직접, 온전히 기록해야 하므로 항상 기본값으로 리셋한다.
        if hasattr(obj.animation_data, "action_influence"):
            obj.animation_data.action_influence = 1.0
        if hasattr(obj.animation_data, "action_blend_type"):
            obj.animation_data.action_blend_type = 'REPLACE'
        if not getattr(w, "bake_overwrite", False):
            _clear_action_keyframes(target_action)

        # [버그 수정] 베이크 루프의 scene.frame_set()이 매 프레임
        # frame_change_pre 핸들러(wiggle_frame_change_handler)를 트리거해서
        # sync_layers()가 베이크 도중에 끼어들어 방금 설정한 활성 액션을
        # 건드리고, 그 여파로 Base 애니메이션 연결이 망가지는 문제가 있었음
        # (자세한 경위는 wiggle_frame_change_handler 주석 참고). 베이크가
        # 끝날 때까지 그 핸들러를 완전히 꺼둔다.
        global _baking_in_progress
        _baking_in_progress = True
        try:
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
        finally:
            _baking_in_progress = False

        obj.animation_data.action = None
        if target_track:
            target_track.mute = target_track_prev_mute if target_track_prev_mute is not None else False
        obj.wiggle_freeze = True
        scene.frame_set(orig_frame)
        sync_layers(obj)
        self.report({'INFO'}, f"Bake Complete into '{target_action.name}': {start_frame}~{end_frame}")
        return {'FINISHED'}


classes = (
    WiggleSimLayer,
    WIGGLE_UL_SimMixLayers,
    WIGGLE_OT_LayerAction,
    WIGGLE_OT_BakeCombined,
)


def register():
    # [버그 수정] register_class가 이미 등록된 클래스에 재호출되면
    # ValueError를 던지는데(Reload Scripts, 비활성화 없이 재등록 등),
    # 여기엔 원래 아무 방어도 없어서 register() 전체가 중간에 멈추고
    # 뒤에 있는 클래스/속성/핸들러 등록이 통째로 스킵됐다.
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass

    bpy.types.Object.wiggle_layers = bpy.props.CollectionProperty(type=WiggleSimLayer)
    bpy.types.Object.wiggle_layer_index = bpy.props.IntProperty(
        name="Idx", default=0, update=update_layer_selection
    )
    # wiggle_influence: 기본값 반드시 1.0. 0.0이면 방금 위글을 켠 본은
    # sync가 한 번 돌기 전까지 물리가 전혀 작동하지 않는다.
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

    # [버그 수정] wiggle_layers(CollectionProperty)가 WiggleSimLayer
    # PropertyGroup 클래스를 참조하고 있는 동안 그 클래스를 먼저
    # unregister_class 하면 예외가 나서 뒤 클래스들이 등록 해제되지 않고
    # 남는다 - 프로퍼티를 클래스보다 먼저 지운다.
    for attr in ("wiggle_layers", "wiggle_layer_index"):
        if hasattr(bpy.types.Object, attr):
            delattr(bpy.types.Object, attr)
    if hasattr(bpy.types.PoseBone, "wiggle_influence"):
        delattr(bpy.types.PoseBone, "wiggle_influence")

    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass


if __name__ == "__main__":
    register()
