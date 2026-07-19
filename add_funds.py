import re

with open(r'C:\Users\gwl\OneDrive\Desktop\fund_predictor_cloud\fund_predictor.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 添加军工和煤炭基金到FUNDS
old_funds = '''FUNDS = [
    {"code": "021753", "name": "南方电力C", "sector": "power", "short": "电力"},
    {"code": "014064", "name": "银华农业C", "sector": "agri", "short": "农业"},
    {"code": "017938", "name": "易方达医疗C", "sector": "med", "short": "医疗"},
    {"code": "015468", "name": "嘉实农业C", "sector": "agri", "short": "农业2"},
]'''

new_funds = '''FUNDS = [
    {"code": "021753", "name": "南方电力C", "sector": "power", "short": "电力"},
    {"code": "014064", "name": "银华农业C", "sector": "agri", "short": "农业"},
    {"code": "017938", "name": "易方达医疗C", "sector": "med", "short": "医疗"},
    {"code": "015468", "name": "嘉实农业C", "sector": "agri", "short": "农业2"},
    {"code": "008886", "name": "富国军工C", "sector": "mil", "short": "军工"},
    {"code": "013596", "name": "招商煤炭C", "sector": "coal", "short": "煤炭"},
]'''

content = content.replace(old_funds, new_funds)

# 添加军工和煤炭板块到SECTORS
old_sectors = '''SECTORS = {
    "power": {"name": "电力", "index": "sz399808"},
    "agri":  {"name": "农业", "index": "sh000949"},
    "med":   {"name": "医药", "index": "sz399989"},
}'''

new_sectors = '''SECTORS = {
    "power": {"name": "电力", "index": "sz399808"},
    "agri":  {"name": "农业", "index": "sh000949"},
    "med":   {"name": "医药", "index": "sz399989"},
    "mil":   {"name": "军工", "index": "sz399959"},
    "coal":  {"name": "煤炭", "index": "sz399998"},
}'''

content = content.replace(old_sectors, new_sectors)

# 添加最优参数
old_params = '''BEST_PARAMS = {
    "021753": (20, 0.01), "014064": (26, 0.01),
    "017938": (30, 0.01), "015468": (30, 1.00),
}'''

new_params = '''BEST_PARAMS = {
    "021753": (20, 0.01), "014064": (26, 0.01),
    "017938": (30, 0.01), "015468": (30, 1.00),
    "008886": (30, 0.01), "013596": (30, 0.01),
}'''

content = content.replace(old_params, new_params)

with open(r'C:\Users\gwl\OneDrive\Desktop\fund_predictor_cloud\fund_predictor.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("添加成功！")
print("新增基金：")
print("  - 008886 富国军工C (军工)")
print("  - 013596 招商煤炭C (煤炭)")
