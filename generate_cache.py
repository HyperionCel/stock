"""一次性脚本：在电脑上运行，生成股票缓存 CSV 文件
运行方式：python generate_cache.py
"""
import akshare as ak
import pandas as pd
from pypinyin import lazy_pinyin, Style

def pinyin(text):
    try:
        initials = lazy_pinyin(text, style=Style.FIRST_LETTER)
        full = lazy_pinyin(text, style=Style.NORMAL)
        return "".join(initials).lower(), "".join(full).lower()
    except:
        return "", ""

# A股
print("正在加载A股列表...")
a_df = ak.stock_info_a_code_name()
a_records = []
for _, row in a_df.iterrows():
    code = str(row["code"]).strip().zfill(6)
    name = str(row["name"]).strip()
    pi, pf = pinyin(name)
    a_records.append({"code": code, "name": name, "pinyin": pi, "pinyin_full": pf, "market": "A股"})
a_out = pd.DataFrame(a_records)
a_out.to_csv("a_stocks_cache.csv", index=False)
print(f"A股缓存已生成: {len(a_records)}条 -> a_stocks_cache.csv")

# 港股
print("正在加载港股列表（99页，约2分钟）...")
hk_df = ak.stock_hk_spot()
col_code = hk_df.columns[1]
col_name = hk_df.columns[2]
hk_records = []
for _, row in hk_df.iterrows():
    code = str(row[col_code]).strip().zfill(5)
    name = str(row[col_name]).strip() if pd.notna(row[col_name]) else str(row[hk_df.columns[3]]).strip()
    if name and code:
        pi, pf = pinyin(name)
        hk_records.append({"code": code, "name": name, "pinyin": pi, "pinyin_full": pf, "market": "港股"})
hk_out = pd.DataFrame(hk_records)
hk_out.to_csv("hk_stocks_cache.csv", index=False)
print(f"港股缓存已生成: {len(hk_records)}条 -> hk_stocks_cache.csv")

print("全部完成！")
