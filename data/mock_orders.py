"""
电商退货退款智能体 · Mock 订单数据（20条，覆盖全部8类测试场景）

字段说明：
  order_id           订单号
  user_id            用户ID
  product_name       商品名称
  product_category   商品类目
  price              订单金额(元)
  purchase_date      购买日期
  purchase_date_days_ago  购买距今天数
  package_status     包裹状态 (unopened/opened_intact/damaged)
  activated          是否已激活 (0/1)
  status             订单状态
  has_repeat_return_30d  30天内是否有退货记录
  order_already_returned 是否已完成退货（幂等保护）
"""

ORDERS = [
    # ═══ 场景1: 正常退货（未拆封，3天前）═══
    {"order_id":"ORD001","user_id":"U001","product_name":"纯棉T恤-白色M码",
     "product_category":"服装","price":89.00,"purchase_date":"2026-05-24",
     "purchase_date_days_ago":3,"package_status":"unopened","activated":0,
     "status":"delivered","has_repeat_return_30d":False,"order_already_returned":False},

    # ═══ 场景2: 质量问题（今天到货的碎屏手机）═══
    {"order_id":"ORD002","user_id":"U002","product_name":"华为Mate70 Pro",
     "product_category":"手机","price":3999.00,"purchase_date":"2026-05-27",
     "purchase_date_days_ago":0,"package_status":"unopened","activated":0,
     "status":"delivered","has_repeat_return_30d":False,"order_already_returned":False},

    # ═══ 场景3: 超7天退货（15天前）═══
    {"order_id":"ORD003","user_id":"U003","product_name":"运动跑鞋-黑色42码",
     "product_category":"鞋靴","price":329.00,"purchase_date":"2026-05-12",
     "purchase_date_days_ago":10,"package_status":"unopened","activated":0,
     "status":"delivered","has_repeat_return_30d":False,"order_already_returned":False},

    # ═══ 场景4: 内衣拒退（5天前）═══
    {"order_id":"ORD004","user_id":"U004","product_name":"莫代尔内衣套装-L码",
     "product_category":"内衣/泳衣","price":128.00,"purchase_date":"2026-05-22",
     "purchase_date_days_ago":5,"package_status":"unopened","activated":0,
     "status":"delivered","has_repeat_return_30d":False,"order_already_returned":False},

    # ═══ 场景5: 已拆封退货（耳机，4天前）═══
    {"order_id":"ORD005","user_id":"U005","product_name":"蓝牙降噪耳机",
     "product_category":"数码配件","price":499.00,"purchase_date":"2026-05-23",
     "purchase_date_days_ago":4,"package_status":"opened_intact","activated":0,
     "status":"delivered","has_repeat_return_30d":False,"order_already_returned":False},

    # ═══ 场景6: 重复退货（30天内有退货记录）═══
    {"order_id":"ORD006","user_id":"U006","product_name":"洗碗机清洁剂6瓶装",
     "product_category":"日用品","price":59.90,"purchase_date":"2026-05-20",
     "purchase_date_days_ago":7,"package_status":"unopened","activated":0,
     "status":"delivered","has_repeat_return_30d":True,"order_already_returned":False},

    # ═══ 场景7: 金额超限（6000元相机）═══
    {"order_id":"ORD007","user_id":"U007","product_name":"索尼A7M4全画幅相机",
     "product_category":"数码影像","price":5999.00,"purchase_date":"2026-05-25",
     "purchase_date_days_ago":2,"package_status":"unopened","activated":0,
     "status":"delivered","has_repeat_return_30d":False,"order_already_returned":False},

    # ═══ 场景8: 换货场景（尺码不对）═══
    {"order_id":"ORD008","user_id":"U008","product_name":"牛仔裤-蓝色30码",
     "product_category":"服装","price":259.00,"purchase_date":"2026-05-24",
     "purchase_date_days_ago":3,"package_status":"unopened","activated":0,
     "status":"delivered","has_repeat_return_30d":False,"order_already_returned":False},

    # ═══ 额外场景: 食品拒退 ═══
    {"order_id":"ORD009","user_id":"U009","product_name":"进口车厘子3J级2斤装",
     "product_category":"食品/生鲜","price":168.00,"purchase_date":"2026-05-26",
     "purchase_date_days_ago":1,"package_status":"unopened","activated":0,
     "status":"delivered","has_repeat_return_30d":False,"order_already_returned":False},

    # ═══ 额外场景: 定制商品拒退 ═══
    {"order_id":"ORD010","user_id":"U010","product_name":"定制情侣手链刻字版",
     "product_category":"定制/DIY","price":199.00,"purchase_date":"2026-05-21",
     "purchase_date_days_ago":6,"package_status":"unopened","activated":0,
     "status":"delivered","has_repeat_return_30d":False,"order_already_returned":False},

    # ═══ 额外场景: 虚拟商品拒退 ═══
    {"order_id":"ORD011","user_id":"U011","product_name":"王者荣耀6480点券充值",
     "product_category":"虚拟商品/充值卡","price":648.00,"purchase_date":"2026-05-27",
     "purchase_date_days_ago":0,"package_status":"unopened","activated":0,
     "status":"delivered","has_repeat_return_30d":False,"order_already_returned":False},

    # ═══ 额外场景: 已激活手机拒退 ═══
    {"order_id":"ORD012","user_id":"U012","product_name":"iPhone16 Pro Max",
     "product_category":"手机","price":8999.00,"purchase_date":"2026-05-20",
     "purchase_date_days_ago":7,"package_status":"opened_intact","activated":1,
     "status":"delivered","has_repeat_return_30d":False,"order_already_returned":False},

    # ═══ 额外场景: 正常退货（未拆封，1天前，低价）═══
    {"order_id":"ORD013","user_id":"U013","product_name":"创意马克杯礼盒装",
     "product_category":"家居用品","price":39.90,"purchase_date":"2026-05-26",
     "purchase_date_days_ago":1,"package_status":"unopened","activated":0,
     "status":"delivered","has_repeat_return_30d":False,"order_already_returned":False},

    # ═══ 额外场景: 运输损坏 ═══
    {"order_id":"ORD014","user_id":"U014","product_name":"液晶显示器支架",
     "product_category":"数码配件","price":189.00,"purchase_date":"2026-05-26",
     "purchase_date_days_ago":1,"package_status":"damaged","activated":0,
     "status":"delivered","has_repeat_return_30d":False,"order_already_returned":False},

    # ═══ 额外场景: 已拆封但完整，不想要了 ═══
    {"order_id":"ORD015","user_id":"U015","product_name":"智能手环运动版",
     "product_category":"数码配件","price":299.00,"purchase_date":"2026-05-23",
     "purchase_date_days_ago":4,"package_status":"opened_intact","activated":0,
     "status":"delivered","has_repeat_return_30d":False,"order_already_returned":False},

    # ═══ 额外场景: 尺码不对+已拆封 ═══
    {"order_id":"ORD016","user_id":"U016","product_name":"女士风衣-驼色M码",
     "product_category":"服装","price":599.00,"purchase_date":"2026-05-25",
     "purchase_date_days_ago":2,"package_status":"opened_intact","activated":0,
     "status":"delivered","has_repeat_return_30d":False,"order_already_returned":False},

    # ═══ 额外场景: 发错货 ═══
    {"order_id":"ORD017","user_id":"U017","product_name":"机械键盘青轴87键",
     "product_category":"数码配件","price":369.00,"purchase_date":"2026-05-26",
     "purchase_date_days_ago":1,"package_status":"unopened","activated":0,
     "status":"delivered","has_repeat_return_30d":False,"order_already_returned":False},

    # ═══ 额外场景: 10天前在宽限期内 ═══
    {"order_id":"ORD018","user_id":"U018","product_name":"瑜伽垫加厚防滑",
     "product_category":"运动户外","price":89.00,"purchase_date":"2026-05-17",
     "purchase_date_days_ago":10,"package_status":"unopened","activated":0,
     "status":"delivered","has_repeat_return_30d":False,"order_already_returned":False},

    # ═══ 额外场景: 食品在窗口期内但黑名单 ═══
    {"order_id":"ORD019","user_id":"U019","product_name":"麻辣小龙虾3斤装",
     "product_category":"食品/生鲜","price":138.00,"purchase_date":"2026-05-26",
     "purchase_date_days_ago":1,"package_status":"unopened","activated":0,
     "status":"delivered","has_repeat_return_30d":False,"order_already_returned":False},

    # ═══ 额外场景: 正常大额退货（4999元，2天前）═══
    {"order_id":"ORD020","user_id":"U020","product_name":"戴森V15无线吸尘器",
     "product_category":"家用电器","price":4999.00,"purchase_date":"2026-05-25",
     "purchase_date_days_ago":2,"package_status":"unopened","activated":0,
     "status":"delivered","has_repeat_return_30d":False,"order_already_returned":False},
]
