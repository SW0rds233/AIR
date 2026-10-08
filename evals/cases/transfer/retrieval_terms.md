# 检索领域词表（用例设计参考）

> 本文件记录盲测时应检查的领域与同词异域边界。运行时加载的词表是
> `src/rag/domain_terms/perovskite-stability.md`；修改本文件不会改变检索行为。
> 新增领域时应在运行时目录新增词表，并通过用例检查其效果。
>
> 每个字段用 `;` 或 `,` 分隔、可写中英变体；英文按词边界匹配，中文按子串匹配。
>
> `domain` 只用于把**主题文本**匹配到本文件（slug 或别名命中即采用）。

domain: perovskite-stability
aliases: 钙钛矿稳定性, 钙钛矿太阳能电池, 钙钛矿电池, 湿度稳定性, 湿度降解, perovskite stability, perovskite solar cell, moisture stability, moisture degradation

## 1. 领域信号词（in_domain）

出现即视为"该领域内的文本"：

in_domain: perovskite; halide; photovoltaic; solar cell; absorber; moisture; humidity; hydration; degradation; decomposition; passivation; grain boundary; iodide; lead iodide; pbi2; hysteresis; power conversion efficiency; pce; thin film; 钙钛矿; 光伏; 太阳能电池; 电池; 湿度; 水合; 降解; 分解; 钝化; 晶界; 碘化铅; 薄膜; 光电转换

## 2. 同词异域术语（off_domain_confusables）

**同一个词在另一领域有完全不同的含义**时，标题命中右侧术语即可疑；只有
`strong_in_domain` 词**就近出现**（同一标题内、间隔不超过 40 字）才判为该领域内的边缘工作。
宁可放过，不可误删。

`perovskite` / `钙钛矿` 的经典混淆：地学/矿物学中的钙钛矿指**地幔矿物相**
（与地震学、高压矿物学、地球内部结构论文共用 "perovskite" 一词），与光伏无关。

strong_in_domain: halide perovskite; perovskite solar; perovskite photovoltaic; 钙钛矿太阳能; 钙钛矿电池

off_domain_confusables: mantle; lower mantle; upper mantle; geophysics; seismic; seismology; mineral physics; high pressure mineral; earth interior; d" layer; bridgmanite; 地幔; 矿物学; 地球物理; 地震; 高压矿物; 地球内部

## 3. 非技术性来源

venue 命中即剔除（教学/人文类来源混入技术综述）：

non_technical_venues: 教学; 教育; 人文; 社会研究; 课程; 教改; 课堂
