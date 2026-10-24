"""Deterministically generate evals/digital_life/memory_cases.json.

DL-P2.6 fixed case set (audit row 6): 100 synthetic facts / 100 questions.
Structure: 20 static fact-domains x 4 facts + 5 evolving domains x 4 facts
(old value, new value, 2 supporting facts) = 100 facts, organised as 30
topic summaries (20 static + 5 current + 5 superseded fork duplicates),
written through 55 real upserts (static topics grow 2+2; evolving current
topics are updated in place old->new; forks hold the superseded value).

Synthetic persona only — no real user data. Pure deterministic construction
(fixed literal tables, no RNG), so re-running must produce byte-identical
JSON; the committed test asserts this.

Run from the repository root:

    python -B evals/digital_life/generate_memory_cases.py
"""

from __future__ import annotations

import json
from pathlib import Path

SEED = 20261024  # documented fixed seed; construction itself is RNG-free

OUT = Path(__file__).resolve().parent / 'memory_cases.json'

# 20 static domains: (key, title, keywords, [4 facts])
DOMAINS = [
    ('fav-color', '最喜欢的颜色', ['颜色', '偏好', '蓝色'], [
        '阿豪最喜欢的颜色是蓝色。',
        '阿豪觉得蓝色让人平静，适合工作环境。',
        '买衣物时阿豪优先选深蓝色。',
        '阿豪的电脑桌面壁纸常年是蓝色调。']),
    ('fav-food', '最喜欢的食物', ['食物', '偏好', '意面'], [
        '阿豪最喜欢的食物是意大利面。',
        '阿豪每周至少吃两次意大利面。',
        '阿豪最常做的意面是培根蛋面。',
        '阿豪吃意面喜欢配气泡水。']),
    ('birth-city', '出生城市', ['出生', '家乡', '槟城'], [
        '阿豪出生在马来西亚槟城。',
        '阿豪在槟城生活到十八岁。',
        '阿豪的出生医院是槟城中央医院。',
        '阿豪童年常去槟城的海边。']),
    ('current-city', '居住城市', ['居住', '搬家', '米兰'], [
        '阿豪现在住在意大利米兰。',
        '阿豪在米兰的公寓靠近运河区。',
        '阿豪搬来米兰已经四年。',
        '阿豪的米兰住址邮编是20144。']),
    ('job', '职业', ['工作', '职业', '工程师'], [
        '阿豪是一名后端工程师。',
        '阿豪主要写 Python 和 Go。',
        '阿豪负责支付系统的稳定性。',
        '阿豪每周三参加技术评审会。']),
    ('company', '公司', ['公司', '雇主', 'Brightline'], [
        '阿豪在 Brightline Labs 工作。',
        '阿豪的工位在三层靠窗位置。',
        '阿豪的公司在米兰岛区办公。',
        '阿豪的直属经理叫 Elena。']),
    ('hobby-photography', '摄影爱好', ['摄影', '镜头', '街头'], [
        '阿豪的爱好是街头摄影。',
        '阿豪常用 35mm 定焦镜头拍街头。',
        '阿豪的相机是 Ricoh GR III。',
        '阿豪每月整理一次照片库。']),
    ('hobby-running', '跑步习惯', ['跑步', '清晨', '晨跑'], [
        '阿豪每周跑步三次。',
        '阿豪通常在清晨六点跑步。',
        '阿豪沿运河路线跑步。',
        '阿豪参加过去年的米兰半马。']),
    ('fav-music', '喜欢的音乐', ['音乐', '乐队', '后摇'], [
        '阿豪最喜欢听 Post-rock。',
        '阿豪最喜欢的乐队是 Explosions in the Sky。',
        '阿豪写代码时听器乐后摇。',
        '阿豪收藏了三十多张黑胶。']),
    ('fav-book', '喜欢的书', ['书', '阅读', '科幻'], [
        '阿豪最喜欢的书是《三体》。',
        '阿豪读过《三体》三遍。',
        '阿豪的枕边书是《球状闪电》。',
        '阿豪偏好硬科幻多于奇幻。']),
    ('pet', '宠物', ['宠物', '猫', 'Pixel'], [
        '阿豪养了一只猫叫 Pixel。',
        'Pixel 是一只英国短毛猫。',
        'Pixel 今年三岁。',
        'Pixel 最爱睡在键盘旁边。']),
    ('car', '汽车', ['车', '通勤', 'Fiat'], [
        '阿豪开一辆蓝色的 Fiat Panda。',
        '阿豪的车是手动挡。',
        '阿豪的车停在公寓地下车库。',
        '阿豪每半年保养一次车。']),
    ('timezone', '时区', ['时区', '作息', '罗马'], [
        '阿豪在 Europe/Rome 时区。',
        '阿豪通常午夜前入睡。',
        '阿豪早上八点半开始工作。',
        '阿豪午休习惯二十分钟小睡。']),
    ('keyboard', '键盘', ['键盘', 'HHKB', '静电容'], [
        '阿豪用 HHKB 静电容键盘。',
        '阿豪习惯 HHKB 的 Control 键布局。',
        '阿豪的键盘用了五年。',
        '阿豪备用键盘是客制化的 65 配列。']),
    ('editor', '编辑器', ['编辑器', '开发', 'VS Code'], [
        '阿豪主力编辑器是 VS Code。',
        '阿豪在 VS Code 里用 Vim 插件。',
        '阿豪的主题是暗色 Solarized。',
        '阿豪的终端字体用 JetBrains Mono。']),
    ('coffee', '咖啡习惯', ['咖啡', '手冲', '浅烘'], [
        '阿豪每天喝两杯咖啡。',
        '阿豪偏好手冲浅烘咖啡。',
        '阿豪常去的咖啡馆在运河边。',
        '阿豪家里有 V60 滤杯。']),
    ('guitar', '吉他', ['吉他', '乐器', '木吉他'], [
        '阿豪会弹木吉他。',
        '阿豪的吉他是 Yamaha FG800。',
        '阿豪每晚练琴十五分钟。',
        '阿豪会弹的第一首歌是《丁香花》。']),
    ('bank', '开户行', ['银行', '账户', 'Intesa'], [
        '阿豪的工资卡在 Intesa 银行。',
        '阿豪的银行网点在中央车站旁。',
        '阿豪的储蓄账户也在 Intesa。',
        '阿豪每月发薪日是二十七号。']),
    ('language', '语言', ['语言', '中文', '意大利语'], [
        '阿豪会说中文、英文和意大利语。',
        '阿豪的意大利语带托斯卡纳口音。',
        '阿豪日常思考用中文。',
        '阿豪正在学第四门语言西班牙语。']),
    ('allergy', '过敏', ['过敏', '芒果', '饮食限制'], [
        '阿豪对芒果过敏。',
        '阿豪随身带抗过敏药。',
        '阿豪吃东南亚菜会先问配料。',
        '阿豪的过敏是六岁时发现的。']),
]

# 5 evolving domains: current topic is updated in place (old -> new value);
# a forked duplicate topic keeps the superseded value (realistic same-domain
# fork the recall path must survive).
EVOLVING = {
    'phone': {
        'title': '手机', 'keywords': ['手机', '设备', '换机'],
        'support': ['阿豪的手机号尾号是 7788。', '阿豪给手机贴了磨砂膜。'],
        'old': '阿豪的手机是 Pixel 8。', 'new': '阿豪现在用 iPhone 17。',
        'fork_key': 'phone-old', 'fork_title': '手机（旧）',
        'fork_summary': '阿豪的手机是 Pixel 8（2026 年之前的旧记录）。',
    },
    'laptop': {
        'title': '笔记本电脑', 'keywords': ['笔记本', '电脑', '开发机'],
        'support': ['阿豪的电脑包是 Tomtoc 的。', '阿豪给笔记本贴了防窥膜。'],
        'old': '阿豪的笔记本是 ThinkPad X1。', 'new': '阿豪现在用 MacBook Air M4。',
        'fork_key': 'laptop-old', 'fork_title': '笔记本电脑（旧）',
        'fork_summary': '阿豪的笔记本是 ThinkPad X1（已退役的旧记录）。',
    },
    'gym': {
        'title': '健身房', 'keywords': ['健身', '训练', '力量'],
        'support': ['阿豪每周练三次力量。', '阿豪的教练叫 Marco。'],
        'old': '阿豪在 FitPlus 健身房办了卡。', 'new': '阿豪现在在 Verde Gym 健身。',
        'fork_key': 'gym-membership', 'fork_title': '健身房会员（旧）',
        'fork_summary': '阿豪在 FitPlus 健身房办过卡（已退卡的旧记录）。',
    },
    'dentist': {
        'title': '牙医', 'keywords': ['牙医', '口腔', '复诊'],
        'support': ['阿豪每半年洗一次牙。', '阿豪用电动牙刷。'],
        'old': '阿豪的牙医是 Rossi 医生。', 'new': '阿豪现在的牙医是 Bianchi 医生。',
        'fork_key': 'dentist-rossi', 'fork_title': '牙医 Rossi（旧）',
        'fork_summary': '阿豪的牙医是 Rossi 医生（已退休的旧记录）。',
    },
    'desk': {
        'title': '书桌', 'keywords': ['书桌', '书房', '办公桌'],
        'support': ['阿豪的书桌靠窗摆放。', '阿豪桌上有一盏屏幕挂灯。'],
        'old': '阿豪用宜家升降桌。', 'new': '阿豪现在用 Oaksam 实木书桌。',
        'fork_key': 'desk-old', 'fork_title': '书桌（旧）',
        'fork_summary': '阿豪用过宜家升降桌（已出二手的旧记录）。',
    },
}

# 40 direct questions: 2 per static domain (facts 1 and 3).
DIRECT_Q = [
    ('fav-color', '阿豪最喜欢的颜色是什么？', '蓝色'),
    ('fav-color', '阿豪买衣物时优先选什么颜色？', '深蓝色'),
    ('fav-food', '阿豪最喜欢的食物是什么？', '意大利面'),
    ('fav-food', '阿豪最常做的意面是哪种？', '培根蛋面'),
    ('birth-city', '阿豪出生在哪个城市？', '槟城'),
    ('birth-city', '阿豪的出生医院是哪家？', '槟城中央医院'),
    ('current-city', '阿豪现在住在哪个城市？', '米兰'),
    ('current-city', '阿豪的米兰住址邮编是多少？', '20144'),
    ('job', '阿豪的职业是什么？', '后端工程师'),
    ('job', '阿豪负责什么系统的稳定性？', '支付系统'),
    ('company', '阿豪在哪家公司工作？', 'Brightline Labs'),
    ('company', '阿豪的直属经理叫什么？', 'Elena'),
    ('hobby-photography', '阿豪的爱好是什么？', '街头摄影'),
    ('hobby-photography', '阿豪的相机是什么型号？', 'Ricoh GR III'),
    ('hobby-running', '阿豪每周跑步几次？', '三次'),
    ('hobby-running', '阿豪参加过哪场半马？', '米兰半马'),
    ('fav-music', '阿豪最喜欢什么风格的音乐？', 'Post-rock'),
    ('fav-music', '阿豪收藏了多少张黑胶？', '三十多张'),
    ('fav-book', '阿豪最喜欢的书是什么？', '三体'),
    ('fav-book', '阿豪的枕边书是哪本？', '球状闪电'),
    ('pet', '阿豪养了什么宠物？', '猫'),
    ('pet', 'Pixel 今年几岁？', '三岁'),
    ('car', '阿豪开什么车？', 'Fiat Panda'),
    ('car', '阿豪的车平时停在哪里？', '地下车库'),
    ('timezone', '阿豪在哪个时区？', 'Europe/Rome'),
    ('timezone', '阿豪早上几点开始工作？', '八点半'),
    ('keyboard', '阿豪用什么键盘？', 'HHKB'),
    ('keyboard', '阿豪的备用键盘是什么配列？', '65配列'),
    ('editor', '阿豪的主力编辑器是什么？', 'VS Code'),
    ('editor', '阿豪的终端字体是什么？', 'JetBrains Mono'),
    ('coffee', '阿豪每天喝几杯咖啡？', '两杯'),
    ('coffee', '阿豪家里有什么咖啡器具？', 'V60滤杯'),
    ('guitar', '阿豪会弹什么乐器？', '木吉他'),
    ('guitar', '阿豪每晚练琴多久？', '十五分钟'),
    ('bank', '阿豪的工资卡在哪家银行？', 'Intesa'),
    ('bank', '阿豪的每月发薪日是哪天？', '二十七号'),
    ('language', '阿豪正在学的第四门语言是什么？', '西班牙语'),
    ('language', '阿豪的意大利语带什么口音？', '托斯卡纳'),
    ('allergy', '阿豪对什么过敏？', '芒果'),
    ('allergy', '阿豪的过敏是几岁时发现的？', '六岁'),
]

# 20 paraphrase questions: 1 per static domain, reworded.
PARAPHRASE_Q = [
    ('fav-color', '如果要送阿豪一件衣服，最稳妥的颜色选择是什么？', '蓝色'),
    ('fav-food', '请阿豪吃饭，最保险的主食是什么？', '意大利面'),
    ('birth-city', '阿豪的人生起点在哪座城市？', '槟城'),
    ('current-city', '阿豪如今定居在意大利的哪座城市？', '米兰'),
    ('job', '阿豪靠什么手艺吃饭？', '后端工程师'),
    ('company', '阿豪的雇主叫什么名字？', 'Brightline Labs'),
    ('hobby-photography', '拿着相机扫街是阿豪的什么？', '爱好'),
    ('hobby-running', '清晨的运河边阿豪在做什么运动？', '跑步'),
    ('fav-music', '阿豪耳机里常循环的是哪类音乐？', 'Post-rock'),
    ('fav-book', '哪本科幻小说让阿豪翻了很多遍？', '三体'),
    ('pet', '阿豪家里的毛孩子叫什么？', 'Pixel'),
    ('car', '阿豪的代步工具是什么牌子的车？', 'Fiat'),
    ('timezone', '和阿豪约会议要按哪个时区？', 'Europe/Rome'),
    ('keyboard', '阿豪敲代码用哪块键盘？', 'HHKB'),
    ('editor', '阿豪写代码打开的第一个应用是什么？', 'VS Code'),
    ('coffee', '阿豪在家自己怎么泡咖啡？', 'V60'),
    ('guitar', '阿豪会摆弄什么弦乐器？', '木吉他'),
    ('bank', '阿豪的钱主要存在哪家银行？', 'Intesa'),
    ('language', '阿豪日常思考用哪种语言？', '中文'),
    ('allergy', '哪种水果阿豪碰不得？', '芒果'),
]

# 20 distractor questions: question explicitly names a sibling domain; the
# answer lives in the target domain (overlapping signals compete).
DISTRACTOR_Q = [
    ('current-city', 'birth-city', '阿豪现在住的城市和出生地是同一座吗？他现在住在哪？', '米兰'),
    ('allergy', 'fav-food', '请阿豪吃饭，除了他爱吃的意面，还要避开什么水果？', '芒果'),
    ('gym', 'hobby-running', '阿豪除了晨跑，现在在哪家健身房练力量？', 'Verde Gym'),
    ('pet', 'allergy', '阿豪对芒果过敏，那他家的英国短毛猫叫什么？', 'Pixel'),
    ('fav-color', 'car', '阿豪的车是什么颜色？和他最喜欢的颜色一样吗？那个颜色是什么？', '蓝色'),
    ('editor', 'keyboard', '除了 HHKB 键盘，阿豪写代码的主力编辑器是什么？', 'VS Code'),
    ('coffee', 'guitar', '阿豪练完吉他习惯喝什么？他偏好哪种咖啡？', '手冲浅烘'),
    ('bank', 'company', '阿豪在 Brightline Labs 的工资打到哪家银行？', 'Intesa'),
    ('language', 'current-city', '阿豪在米兰常说的三种语言是什么？', '意大利语'),
    ('fav-music', 'guitar', '阿豪弹木吉他时最喜欢伴奏哪类音乐？', 'Post-rock'),
    ('job', 'editor', '阿豪当后端工程师主要写哪两种语言？', 'Python'),
    ('fav-book', 'language', '《三体》阿豪是用哪种语言读的？', '中文'),
    ('hobby-photography', 'pet', '阿豪给家里的猫拍照常用什么镜头？', '35mm'),
    ('timezone', 'hobby-running', '按阿豪的时区，他清晨几点跑步？', '六点'),
    ('keyboard', 'job', '阿豪写 Python 和 Go 代码用哪块键盘？', 'HHKB'),
    ('guitar', 'fav-music', '听 Explosions in the Sky 时阿豪会弹什么乐器？', '木吉他'),
    ('birth-city', 'language', '阿豪在槟城长大先学会的是什么语言？', '中文'),
    ('bank', 'current-city', '阿豪在米兰的银行网点在哪个地标旁？', '中央车站'),
    ('desk', 'company', '阿豪远程为 Brightline Labs 办公时用什么书桌？', 'Oaksam'),
    ('coffee', 'timezone', '阿豪每天两杯咖啡是按哪个时区的作息算的？', 'Europe/Rome'),
]

# 20 outdated questions: 4 phrasing variants x 5 evolving domains.
OUTDATED_VARIANTS = [
    '阿豪现在用的是什么{noun}？',
    '{noun}方面，阿豪目前的情况是什么？',
    '阿豪最新的{noun}是什么？',
    '阿豪当前的{noun}安排是怎样的？',
]
OUTDATED_NOUNS = {'phone': '手机', 'laptop': '笔记本', 'gym': '健身房', 'dentist': '牙医', 'desk': '书桌'}


def build():
    writes = []
    topics_meta = []
    questions = []

    # Static domains: topic grows 2 facts -> 4 facts (2 upserts each).
    for key, title, kws, facts in DOMAINS:
        writes.append({'topic': key, 'title': title, 'keywords': list(kws),
                       'summary': ' '.join(facts[:2]), 'phase': 'initial'})
        writes.append({'topic': key, 'title': title, 'keywords': list(kws),
                       'summary': ' '.join(facts), 'phase': 'grow'})
        topics_meta.append({'key': key, 'title': title, 'keywords': list(kws),
                            'facts': list(facts), 'kind': 'static'})

    # Evolving domains: current topic old->new in place + stale fork.
    for key, spec in EVOLVING.items():
        writes.append({'topic': key, 'title': spec['title'], 'keywords': list(spec['keywords']),
                       'summary': ' '.join(spec['support'] + [spec['old']]), 'phase': 'initial'})
        writes.append({'topic': key, 'title': spec['title'], 'keywords': list(spec['keywords']),
                       'summary': ' '.join(spec['support'] + [spec['new']]), 'phase': 'update'})
        writes.append({'topic': spec['fork_key'], 'title': spec['fork_title'],
                       'keywords': list(spec['keywords']) + ['旧'],
                       'summary': spec['fork_summary'], 'phase': 'stale-fork'})
        topics_meta.append({'key': key, 'title': spec['title'], 'keywords': list(spec['keywords']),
                            'facts': spec['support'] + [spec['new']], 'kind': 'current'})
        topics_meta.append({'key': spec['fork_key'], 'title': spec['fork_title'],
                            'keywords': list(spec['keywords']) + ['旧'],
                            'facts': [spec['fork_summary']], 'kind': 'stale_fork'})

    qid = 0

    def add(category, question, target, **extra):
        nonlocal qid
        qid += 1
        questions.append({'id': f'q{qid:03d}', 'category': category,
                          'question': question, 'target_topic': target, **extra})

    for target, q, a in DIRECT_Q:
        add('direct', q, target, answer_key=a)
    for target, q, a in PARAPHRASE_Q:
        add('paraphrase', q, target, answer_key=a)
    for target, distractor, q, a in DISTRACTOR_Q:
        add('distractor', q, target, answer_key=a, distractor_topic=distractor)
    for variant in OUTDATED_VARIANTS:
        for key, spec in EVOLVING.items():
            add('outdated', variant.format(noun=OUTDATED_NOUNS[key]), key,
                answer_key=spec['new'], current_topic=key,
                stale_topic=spec['fork_key'], stale_answer_key=spec['old'])

    return {
        'meta': {
            'seed': SEED,
            'facts': sum(len(t['facts']) for t in topics_meta),
            'writes': len(writes),
            'topics': len(topics_meta),
            'questions': len(questions),
        },
        'writes': writes,
        'topics': topics_meta,
        'questions': questions,
    }


def main():
    data = build()
    assert data['meta']['facts'] == 100, data['meta']
    assert data['meta']['writes'] == 55, data['meta']
    assert data['meta']['topics'] == 30, data['meta']
    assert data['meta']['questions'] == 100, data['meta']
    by_cat = {c: sum(1 for q in data['questions'] if q['category'] == c)
              for c in ('direct', 'paraphrase', 'distractor', 'outdated')}
    assert by_cat == {'direct': 40, 'paraphrase': 20, 'distractor': 20, 'outdated': 20}, by_cat
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({**data['meta'], 'by_category': by_cat}, indent=2))


if __name__ == '__main__':
    main()
