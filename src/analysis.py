"""
NB-14P 筆電 BOM 降本分析
========================
重現提案報告中的每一個數字：

  1. 資料清洗：找出並修正報價中的 3 個資料錯誤
  2. 成本基準：第一版報價 + 模具費攤提 + 組裝
  3. Pareto 分析：哪些零件佔了 80% 成本
  4. 供應商重選：只換已驗證供應商能省多少
  5. ME 機殼材質：每台總成本（含良率、模具攤提）與損益兩平
  6. 主提案與成本橋：面板降規 + A/C 件改沖壓 + 換供應商
  7. DRAM 漲價敏感度
  8. 備案：客戶堅持 OLED 時的降本路徑

執行：python src/analysis.py
輸出：results/*.csv 與 images/*.png
"""

from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import pandas as pd

matplotlib.use("Agg")
ROOT = Path(__file__).resolve().parent.parent
DATA, RES, IMG = ROOT / "data", ROOT / "results", ROOT / "images"
RES.mkdir(exist_ok=True)
IMG.mkdir(exist_ok=True)

EAU = 300_000      # 年用量（台）
TARGET = 560.0     # 客戶 BOM 目標成本（USD/台）
FATP = 12.0        # 整機組裝，廠內成本

# 圖表樣式
# 中文字型：依序找電腦上有安裝的（Linux / Windows / macOS）
from matplotlib import font_manager
_installed = {f.name for f in font_manager.fontManager.ttflist}
_cjk = [f for f in ["Noto Sans CJK TC", "Microsoft JhengHei", "PingFang TC", "Heiti TC"] if f in _installed]
plt.rcParams.update({
    "font.family": _cjk + ["sans-serif"],
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": "#c3c2b7", "axes.labelcolor": "#52514e",
    "xtick.color": "#52514e", "ytick.color": "#52514e",
    "axes.titlesize": 14, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "figure.dpi": 150, "savefig.bbox": "tight", "figure.facecolor": "white",
})
BLUE, ORANGE, GRAY, INK, MUTED = "#2a78d6", "#eb6834", "#b8b7ae", "#0b0b0b", "#52514e"

def titles(ax, title, sub):
    """主標題寫結論，副標題寫資料口徑，兩行分開不重疊。"""
    ax.set_title(title, pad=26)
    ax.text(0, 1.015, sub, transform=ax.transAxes, fontsize=9, color=MUTED, va="bottom")


read = lambda name: pd.read_csv(DATA / f"{name}.csv")
bom, quotes, me_opt = read("bom"), read("quotes"), read("me_enclosure_options")
thermal, ee_opt, price_idx = read("thermal_options"), read("ee_options"), read("price_index")

def out(df, name):
    df.to_csv(RES / f"{name}.csv", index=False, encoding="utf-8-sig")
    return df

# ------------------------------------------------------------
# 1. 資料清洗
# ------------------------------------------------------------
q = quotes.copy()
fixes = []

# (a) 單位：以「每 GB」報價的記憶體 → 每顆 4GB
m = q["備註"].fillna("").str.contains("每 GB")
for i in q.index[m]:
    fixes.append((q.at[i, "報價編號"], "單價為每 GB，換算成每顆（×4）", q.at[i, "單價USD"], q.at[i, "單價USD"] * 4))
q.loc[m, "單價USD"] *= 4

# (b) 含稅：扣掉 5% 營業稅
m = q["備註"].fillna("").str.contains("含 5% 營業稅")
for i in q.index[m]:
    fixes.append((q.at[i, "報價編號"], "報價含 5% 稅，換算未稅價", q.at[i, "單價USD"], q.at[i, "單價USD"] / 1.05))
q.loc[m, "單價USD"] /= 1.05

# (c) 模具費異常：低於同零件其他報價中位數的 20% → 視為少打一個 0
nre_med = q.groupby("料號")["模具/NRE費USD"].transform("median")
m = q["模具/NRE費USD"].notna() & (q["模具/NRE費USD"] < 0.2 * nre_med)
for i in q.index[m]:
    fixes.append((q.at[i, "報價編號"], "模具費疑似少打一個 0（待供應商確認）", q.at[i, "模具/NRE費USD"], q.at[i, "模具/NRE費USD"] * 10))
q.loc[m, "模具/NRE費USD"] *= 10

out(pd.DataFrame(fixes, columns=["報價編號", "問題", "原始值", "修正值"]), "01_data_cleaning")

# 每台成本 = 單價 × 每台用量 + 模具費 ÷ 年用量
q = q.merge(bom[["料號", "分類", "每台用量"]], on="料號")
q["每台成本"] = q["單價USD"] * q["每台用量"] + q["模具/NRE費USD"].fillna(0) / EAU
q["需驗證"] = q["備註"].fillna("").str.contains("跨業報價")

# ------------------------------------------------------------
# 2. 成本基準（第一版報價）
# ------------------------------------------------------------
v1 = q[q["第一版採用"]].copy()
baseline_parts = v1["每台成本"].sum()
baseline = baseline_parts + FATP
nre_total = (v1["模具/NRE費USD"].fillna(0) / EAU).sum()

# ------------------------------------------------------------
# 3. Pareto
# ------------------------------------------------------------
p = pd.concat([v1[["零件名稱", "分類", "每台成本"]],
               pd.DataFrame([{"零件名稱": "整機組裝 FATP", "分類": "組裝", "每台成本": FATP}])],
              ignore_index=True)
p = p.sort_values("每台成本", ascending=False).reset_index(drop=True)
p["佔比"] = p["每台成本"] / baseline
p["累計"] = p["佔比"].cumsum()
out(p.round(4), "02_pareto")
n80 = int((p["累計"] < 0.8).sum() + 1)

top = p.head(15)
fig, ax = plt.subplots(figsize=(11, 5.2))
x = range(len(top))
colors = [BLUE if i < n80 else GRAY for i in x]
ax.bar(x, top["佔比"] * 100, color=colors, width=0.7, label="單項佔比")
ax.plot(x, top["累計"] * 100, color=ORANGE, lw=2, marker="o", ms=4, label="累計佔比")
for i in [0, 1, n80 - 1]:
    ax.annotate(f"{top['累計'].iloc[i]:.0%}", (i, top["累計"].iloc[i] * 100), xytext=(0, 8),
                textcoords="offset points", ha="center", fontsize=9, color=INK)
ax.axhline(80, color=MUTED, lw=0.8, ls="--")
ax.set_ylim(0, 100)
ax.set_xticks(list(x), top["零件名稱"], rotation=40, ha="right", fontsize=9)
ax.set_ylabel("佔整機成本（%）")
ax.legend(frameon=False, loc="center right")
titles(ax, f"前 {n80} 項零件佔整機成本 80%，CPU 與面板就佔將近一半",
       f"第一版 BOM USD {baseline:.2f}/台（含模具費攤提）；藍色為累計到 80% 的零件，顯示前 15 項")
fig.savefig(IMG / "01_pareto.png")
plt.close(fig)

# ------------------------------------------------------------
# 4. 供應商重選（只換已驗證供應商）
# ------------------------------------------------------------
rows = []
for part, g in q.groupby("料號"):
    cur = g[g["第一版採用"]].iloc[0]
    ok = g[~g["需驗證"]]
    best = ok.loc[ok["每台成本"].idxmin()]
    best_any = g.loc[g["每台成本"].idxmin()]
    rows.append({"料號": part, "零件名稱": cur["零件名稱"], "分類": cur["分類"],
                 "第一版": cur["品牌/製造商"], "第一版每台": cur["每台成本"],
                 "建議（已驗證）": best["品牌/製造商"], "建議每台": best["每台成本"],
                 "省（已驗證）": cur["每台成本"] - best["每台成本"],
                 "省（含需驗證）": cur["每台成本"] - best_any["每台成本"]})
sw = pd.DataFrame(rows)
sw = sw[sw["省（含需驗證）"] > 0.005].sort_values("省（含需驗證）", ascending=False)
out(sw.round(3), "03_supplier_switch")

# ------------------------------------------------------------
# 5. ME 機殼方案：每台總成本與損益兩平
# ------------------------------------------------------------
me = me_opt.copy()
me["變動成本"] = me["單價USD"] / me["量產良率"]
me["每台總成本"] = me["變動成本"] + me["模具/治具費USD"] / EAU
out(me.round(3), "04_me_enclosure_tco")

be = []
for part, g in me.groupby("零件", sort=False):
    cnc, stamp = g.iloc[0], g.iloc[1]
    be.append({"零件": part,
               "模具費多付": stamp["模具/治具費USD"] - cnc["模具/治具費USD"],
               "每台變動成本省": cnc["變動成本"] - stamp["變動成本"],
               "損益兩平台數": (stamp["模具/治具費USD"] - cnc["模具/治具費USD"]) / (cnc["變動成本"] - stamp["變動成本"]),
               "30萬台時每台省": cnc["每台總成本"] - stamp["每台總成本"]})
be = out(pd.DataFrame(be).round(2), "05_breakeven")

fig, ax = plt.subplots(figsize=(9, 4.6))
vols = range(12_000, 200_001, 1_000)
for (part, g), c, dy in zip(list(me.groupby("零件", sort=False))[:2], [BLUE, ORANGE], [-18, 10]):
    cnc, stamp = g.iloc[0], g.iloc[1]
    diff = [(cnc["變動成本"] + cnc["模具/治具費USD"] / v) - (stamp["變動成本"] + stamp["模具/治具費USD"] / v) for v in vols]
    ax.plot([v / 1000 for v in vols], diff, color=c, lw=2, label=part)
    bep = be.loc[be["零件"] == part, "損益兩平台數"].iloc[0]
    ax.plot(bep / 1000, 0, "o", color=c, ms=7)
    ax.annotate(f"{part[:2]}：{bep/1000:.1f} 千台", (bep / 1000, 0), xytext=(8, dy),
                textcoords="offset points", fontsize=9, color=INK)
ax.axhline(0, color=MUTED, lw=0.8)
ax.set_xlabel("年用量（千台）")
ax.set_ylabel("改沖壓後每台省（USD）")
titles(ax, "年用量超過約 3.1 萬台，A/C 件改沖壓就划算",
       "每台總成本 = 單價 ÷ 良率 + 模具費 ÷ 年用量；本案年用量 30 萬台，遠在損益兩平點右側")
ax.legend(frameon=False, loc="lower right")
fig.savefig(IMG / "02_breakeven.png")
plt.close(fig)

# ------------------------------------------------------------
# 6. 主提案與成本橋
# ------------------------------------------------------------
panel_now = v1.loc[v1["零件名稱"] == "面板 Panel", "每台成本"].iloc[0]
panel_new = ee_opt.loc[ee_opt["方案"] == "PNL-2", "每台成本USD"].iloc[0]
s_panel = panel_now - panel_new
s_stamp = be.loc[be["零件"].isin(["A件 上蓋", "C件 鍵盤面"]), "30萬台時每台省"].sum()
# 面板與 A 件已經換方案，它們的「換供應商」省額不能重複計算
s_supplier = sw.loc[~sw["零件名稱"].isin(["面板 Panel", "A件 上蓋"]), "省（已驗證）"].sum()

plan = pd.DataFrame([
    ["EE", "面板降規：2.8K OLED → 2.2K IPS 90Hz（PNL-2）", s_panel],
    ["ME", "A/C 件：鋁 CNC → 鋁沖壓 + 局部 CNC", s_stamp],
    ["EE", "換已驗證供應商（不含面板、A 件）", s_supplier],
], columns=["領域", "措施", "每台省USD"])
plan["年省USD"] = plan["每台省USD"] * EAU
final = baseline - plan["每台省USD"].sum()
out(plan.round(2), "06_main_plan")

fig, ax = plt.subplots(figsize=(10, 5))
labels = ["第一版 BOM", "面板降規", "A/C 件改沖壓", "換供應商", "提案 BOM"]
vals = [baseline, -s_panel, -s_stamp, -s_supplier, final]
level = baseline
for i, (lab, v) in enumerate(zip(labels, vals)):
    if i in (0, len(vals) - 1):
        ax.bar(i, v, color=GRAY, width=0.6)
        ax.text(i, v + 3, f"{v:.2f}", ha="center", fontsize=10, color=INK)
    else:
        ax.bar(i, -v, bottom=level + v, color=BLUE, width=0.6)
        ax.text(i, level + 3, f"{v:.2f}", ha="center", fontsize=10, color=INK)
        level += v
ax.axhline(TARGET, color=ORANGE, lw=1.5, ls="--")
ax.text(len(vals) - 0.6, TARGET + 2, f"目標 {TARGET:.0f}", color=INK, fontsize=9, ha="right")
ax.set_xticks(range(len(labels)), labels)
ax.set_ylim(500, baseline + 15)
ax.set_ylabel("每台 BOM（USD）")
titles(ax, f"三項措施讓 BOM 從 {baseline:.2f} 降到 {final:.2f}，低於目標 {TARGET - final:.2f}",
       "USD/台，縱軸從 500 起；藍色為降本項目")
fig.savefig(IMG / "03_cost_bridge.png")
plt.close(fig)

# ------------------------------------------------------------
# 7. DRAM 漲價敏感度
# ------------------------------------------------------------
mem = v1.loc[v1["零件名稱"] == "記憶體 LPDDR5X", "每台成本"].iloc[0]
sens = pd.DataFrame({"DRAM 漲幅": [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6]})
sens["每台增加"] = mem * sens["DRAM 漲幅"]
sens["提案 BOM"] = final + sens["每台增加"]
sens["是否達標"] = sens["提案 BOM"] <= TARGET
out(sens.round(2), "07_dram_sensitivity")
max_rise = (TARGET - final) / mem

fig, ax = plt.subplots(figsize=(9, 4.4))
x = sens["DRAM 漲幅"] * 100
ax.plot(x, sens["提案 BOM"], color=BLUE, lw=2, marker="o", ms=5)
ax.axhline(TARGET, color=ORANGE, lw=1.5, ls="--")
ax.text(0, TARGET + 0.8, f"目標 {TARGET:.0f}", color=INK, fontsize=9)
ax.axvline(max_rise * 100, color=MUTED, lw=0.8, ls=":")
ax.text(max_rise * 100 + 1, final + 1, f"漲 {max_rise:.0%} 以內仍達標", color=INK, fontsize=9)
ax.set_xlabel("DRAM 價格漲幅（%）")
ax.set_ylabel("提案 BOM（USD/台）")
titles(ax, "記憶體再漲超過約 1/4，提案就會超標",
       f"記憶體現行每台 USD {mem:.2f}；模擬料價指數近 12 個月已漲超過一倍")
fig.savefig(IMG / "04_dram_sensitivity.png")
plt.close(fig)

# ------------------------------------------------------------
# 8. 備案：保留 OLED
# ------------------------------------------------------------
boe = sw.loc[sw["零件名稱"] == "面板 Panel", "省（已驗證）"].iloc[0]
mg = me.set_index("方案")
step1 = (mg.loc["A件-2", "每台總成本"] - mg.loc["A件-3", "每台總成本"]) + (mg.loc["C件-2", "每台總成本"] - mg.loc["C件-3", "每台總成本"])
step2 = mg.loc["D件-2", "每台總成本"] - mg.loc["D件-4", "每台總成本"]
th = thermal.set_index("方案")
step3 = (th.loc["散熱-2", "單價USD"] + th.loc["散熱-2", "模具費USD"] / EAU) - (th.loc["散熱-1", "單價USD"] + th.loc["散熱-1", "模具費USD"] / EAU)
step4 = sw.loc[sw["零件名稱"].isin(["鍵盤", "轉軸 Hinge", "喇叭"]), "省（含需驗證）"].sum()
eo = ee_opt.set_index("方案")
step5 = (eo.loc["PCB-2", "每台成本USD"] / eo.loc["PCB-2", "良率"] - eo.loc["PCB-1", "每台成本USD"] / eo.loc["PCB-1", "良率"]) - 1.8
fb = pd.DataFrame([
    ["起點", "面板換 BOE OLED + A/C 件沖壓 + 換已驗證供應商", boe + s_stamp + s_supplier, "同主提案"],
    ["1", "A/C 件改鎂鋁合金壓鑄（取代沖壓）", step1, "良率 86～88%、開模 12 週、質感「中」"],
    ["2", "D 件改 PC/ABS 塑膠", step2, "不符合鋁合金規格，需客戶同意"],
    ["3", "散熱改單風扇 + 單熱管", step3, "CPU 降到 20W，效能約 -12%"],
    ["4", "鍵盤、轉軸、喇叭改五金廠", step4, "跨業報價，需樣品驗證"],
    ["5", "PCB 改 8 層通孔板（已扣機構 +1.8）", step5, "板面積 +22%，可能壓縮電池空間"],
], columns=["步驟", "措施", "每台省USD", "主要風險"])
fb["累計 BOM"] = baseline - fb["每台省USD"].cumsum()
out(fb.round(2), "08_fallback_keep_oled")

# ------------------------------------------------------------
print(f"資料清洗：修正 {len(fixes)} 筆")
print(f"第一版 BOM：零件 {baseline_parts:.2f}（含模具攤提 {nre_total:.2f}）+ 組裝 {FATP} = {baseline:.2f}")
print(f"Pareto：前 {n80} 項佔 80%")
print(f"主提案：省 {plan['每台省USD'].sum():.2f}/台 → {final:.2f}（目標 {TARGET}，餘裕 {TARGET - final:.2f}）")
print(f"年省：USD {plan['年省USD'].sum() / 1e4:,.0f} 萬")
print(f"DRAM 最多可漲 {max_rise:.1%} 仍達標")
print(f"備案（保留 OLED）最終：{fb['累計 BOM'].iloc[-1]:.2f}")
