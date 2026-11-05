"""栏目跟英文官网首页一致：Basics、The Farm、Environment、Gameplay、Items、The Valley、Beyond the Valley。"""

from __future__ import annotations

SECTIONS: list[dict] = [
    {
        "name": "基础",
        "hubs": [
            ("新手指南", "新手指南", "Animals_Icon.png"),
            ("玩家角色", "玩家角色", "The_Player_Icon.png"),
            ("控制", "控制", "Controls_Icon.png"),
            ("能量", "能量", "32px-Energy.png"),
            ("生命值", "生命值", "32px-Health.png"),
            ("技能", "技能", "32px-Scout.png"),
            ("游戏内时间", "游戏内时间", "30px-Time_Icon.png"),
            ("物品栏", "物品栏", "32px-Inventory_Icon.png"),
            ("农场地图", "农场地图", "32px-Standard_Farm_Map_Icon.png"),
        ],
    },
    {
        "name": "农场",
        "hubs": [
            ("农作物", "农作物", "32px-Corn.png"),
            ("售卖", "售卖", "32px-ShippingBox.png"),
            ("动物", "动物", "32px-White_Chicken.png"),
            ("果树", "果树", "32px-Apple_Sapling.png"),
            ("工匠物品", "工匠物品", "Artisan_Icon.png"),
            ("农舍", "农舍", "32px-House_(tier_2).png"),
            ("山洞", "山洞", "32px-Red_Mushroom.png"),
            ("温室", "温室", "32px-Quality_Sprinkler.png"),
            ("小屋", "小屋", "32px-Plank_Cabin_Stage_1.png"),
        ],
    },
    {
        "name": "环境",
        "hubs": [
            ("天气", "天气", "32px-Weather_Icon.png"),
            ("季节", "季节", "32px-All_Seasons_Icon.png"),
            ("春季", "春季", "32px-Dandelion.png"),
            ("夏季", "夏季", "32px-Sweet_Pea.png"),
            ("秋季", "秋季", "32px-Hazelnut.png"),
            ("冬季", "冬季", "32px-Snow_Yam.png"),
            ("节日", "节日", "Festivals_Icon.png"),
            ("怪物", "怪物", "32px-Bug.png"),
            ("电视", "电视", "32px-Floor_TV.png"),
        ],
    },
    {
        "name": "玩法",
        "hubs": [
            ("居民", "村民", "Abigail_Icon.png"),
            ("友谊", "友谊", "Speech_bubble.png"),
            ("婚姻", "婚姻", "Marriage_Icon.png"),
            ("孩子", "孩子", "Toddler_Icon.png"),
            ("任务", "任务", "Quests_Icon.png"),
            ("收集包", "收集包", "32px-Bundle_Icon.png"),
            ("成就", "成就", "32px-Achievements_Icon.png"),
            ("合作", "多人游戏", "32px-Multiplayer_Icon.png"),
            ("模组:目录", "模组", "32px-SMAPI_mascot.png"),
        ],
    },
    {
        "name": "物品",
        "wide": True,
        "hubs": [
            ("工具", "工具", "32px-Hoe.png"),
            ("武器", "武器", "32px-Rusty_Sword.png"),
            ("帽子", "帽子", "32px-Red_Cowboy_Hat.png"),
            ("鞋类", "鞋类", "32px-Tundra_Boots.png"),
            ("戒指", "戒指", "32px-Sturdy_Ring.png"),
            ("采集", "采集", "32px-Wild_Horseradish.png"),
            ("鱼", "鱼", "32px-Bream.png"),
            ("鱼饵", "鱼饵", "32px-Wild_Bait.png"),
            ("渔具", "渔具", "32px-Dressed_Spinner.png"),
            ("肥料", "肥料", "32px-Basic_Fertilizer.png"),
            ("烹饪", "烹饪", "Cooking_Icon.png"),
            ("打造", "打造", "32px-Field_Snack.png"),
            ("树", "树", "32px-Acorn.png"),
            ("秘密纸条", "秘密纸条", "32px-Secret_Note_Icon.png"),
            ("书", "书籍", "32px-Book_of_Mysteries.png"),
            ("古物", "古物", "32px-Arrowhead.png"),
            ("矿物", "矿物", "32px-Ruby_Node.png"),
            ("家具", "家具", "Furniture_Icon.png"),
            ("墙纸", "墙纸", "Wallpaper_Icon.png"),
            ("地板", "地板", "32px-Flooring_36_Icon.png"),
        ],
    },
    {
        "name": "山谷",
        "columns": [
            [
                ("鹈鹕镇", "鹈鹕镇", "Lewis_Icon.png"),
                ("铁匠铺", "铁匠铺", "Clint_Icon.png"),
                ("社区中心", "社区中心", "32px-Junimo_Icon.png"),
                ("哈维的诊所", "哈维的诊所", "Harvey_Icon.png"),
                ("Joja超市", "Joja超市", "Morris_Icon.png"),
                ("博物馆", "博物馆", "Gunther_Icon.png"),
                ("皮埃尔的杂货店", "皮埃尔的杂货店", "Pierre_Icon.png"),
                ("星之果实酒吧", "星之果实酒吧", "Gus_Icon.png"),
                ("下水道", "下水道", "Krobus_Icon.png"),
            ],
            [
                ("煤矿森林", "煤矿森林", "32px-Forester.png"),
                ("玛妮的牧场", "玛妮的牧场", "Marnie_Icon.png"),
                ("废弃的屋子", "废弃的屋子", "Abandoned_House_Icon.png"),
                ("秘密森林", "秘密森林", "32px-Hardwood.png"),
                ("旅行货车", "旅行货车", "Traveling_Cart_Icon.png"),
                ("法师塔", "法师塔", "Wizard_Icon.png"),
                ("沙滩", "海滩", "32px-Coral.png"),
                ("鱼店", "鱼店", "Willy_Icon.png"),
            ],
            [
                ("顶峰", "深山", "32px-Stone.png"),
                ("探险家公会", "探险家公会", "Marlon_Icon.png"),
                ("木匠的商店", "木匠的商店", "Robin_Icon.png"),
                ("矿井", "矿井", "The_Mines_Icon.png"),
                ("铁路", "铁路", "32px-Railroad_Icon.png"),
                ("温泉", "温泉", "Spa_Icon.png"),
                ("采石场", "采石场", "32px-Copper_Node.png"),
                ("采石场矿井", "采石场矿井", "32px-QuarryMineIcon.png"),
                ("突变虫穴", "突变虫穴", "32px-Mutant_Fly.png"),
                ("女巫小屋", "女巫小屋", "Henchman_Icon.png"),
            ],
        ],
    },
    {
        "name": "谷外",
        "columns": [
            [
                ("沙漠", "沙漠", "32px-Cactus_Fruit.png"),
                ("赌场", "赌场", "Mr._Qi_Icon.png"),
                ("沙漠商人", "沙漠商人", "32px-Desert_Trader_Icon.png"),
                ("绿洲", "绿洲", "Sandy_Icon.png"),
                ("骷髅洞穴", "骷髅洞穴", "32px-Iridium_Node.png"),
            ],
            [
                ("姜岛", "姜岛", "Leo_Icon.png"),
                ("岛屿办事处", "岛屿办事处", "Professor_Snail_Icon.png"),
                ("姜岛商人", "姜岛商人", "Island_Trader_Icon.png"),
                ("齐先生的核桃房", "核桃房", "32px-Golden_Walnut.png"),
                ("火山地牢", "火山地牢", "32px-Cinder_Shard.png"),
            ],
        ],
    },
]

# 先写大类，后写的分类会盖住前面的分组。果树留在农场，沙漠和姜岛留在谷外。
CATEGORY_ROOTS: list[tuple[str, str]] = [
    ("技能", "基础"),
    ("农作物", "农场"),
    ("动物", "农场"),
    ("工匠物品", "农场"),
    ("节日", "环境"),
    ("怪物", "环境"),
    ("NPC", "玩法"),
    ("成就", "玩法"),
    ("工具", "物品"),
    ("武器", "物品"),
    ("帽子", "物品"),
    ("鞋类", "物品"),
    ("戒指", "物品"),
    ("采集", "物品"),
    ("鱼", "物品"),
    ("鱼饵", "物品"),
    ("渔具", "物品"),
    ("肥料", "物品"),
    ("烹饪", "物品"),
    ("可制作物品", "物品"),
    ("树", "物品"),
    ("书", "物品"),
    ("古物", "物品"),
    ("矿物", "物品"),
    ("家具", "物品"),
    ("装潢", "物品"),
    ("地点", "山谷"),
    ("商店", "山谷"),
    ("果树", "农场"),
    ("沙漠地点", "谷外"),
    ("岛屿地点", "谷外"),
]

ALIASES = {
    "玩家": "玩家角色",
    "结婚": "婚姻",
    "背包": "物品栏",
    "模组": "模组:目录",
    "酒吧": "星之果实酒吧",
    "制作": "打造",
    "诊所": "哈维的诊所",
    "鞋": "鞋类",
    "鞋子": "鞋类",
    "海滩": "沙滩",
    "冒险者公会": "探险家公会",
    "山顶": "顶峰",
    "深山": "顶峰",
    "出货箱": "售卖",
    "出货": "售卖",
    "壁纸": "墙纸",
    "体力": "能量",
    "村民": "居民",
    "一天": "游戏内时间",
    "日循环": "游戏内时间",
    "多人游戏": "合作",
    "多人": "合作",
    "树木": "树",
    "书籍": "书",
    "矿洞": "矿井",
    "火山矿场": "火山地牢",
    "火山": "火山地牢",
    "核桃房": "齐先生的核桃房",
    "农场洞穴": "山洞",
    "洞穴": "山洞",
    "岛屿商人": "姜岛商人",
    "田野办公室": "岛屿办事处",
    # 口语/通称 → Wiki 条目名
    "三文鱼": "鲑鱼",
    "salmon": "鲑鱼",
    "大嘴鲈": "大嘴鲈鱼",
    "小嘴鲈": "小嘴鲈鱼",
    "虹鳟": "虹鳟鱼",
}
CATEGORY_ORDER = ["基础", "农场", "环境", "玩法", "物品", "山谷", "谷外"]


def iter_hubs(section: dict):
    if section.get("columns"):
        for column in section["columns"]:
            yield from column
    else:
        yield from section["hubs"]


def hub_group(title: str) -> str | None:
    for section in SECTIONS:
        for page_title, _label, _icon in iter_hubs(section):
            if page_title == title:
                return section["name"]
    return None


def all_hub_titles() -> list[str]:
    titles = []
    for section in SECTIONS:
        for page_title, _label, _icon in iter_hubs(section):
            titles.append(page_title)
    return titles
