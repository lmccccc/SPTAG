# V5 局部 label-posting 层级准入：算法与论文表述交接

> 本文描述当前已实现的 V5，不是新的设计提案，也不是可以直接视为证明的论文正文。
> 核心实现是 `LocalLabelCensus.h` 和 `LayeredLabelHierarchyBuilder.h`。
> 写作 agent 应特别保留以下区别：统计层次与查询 posting 层次、H1 支持密度与原始记录 selectivity、预计候选容量与实际查询可达性。
> 本文不要求修改代码、查询参数或正在运行的 SIFT1B 重建。

## 1. 核心思想

保留原有 H1 图及 SSD 数据层，在其上构建 H2–H5 相邻层 label postings。
是否将一个标签继续向上组织，不再由该标签的全局原始记录频率决定，而由其所在空间区域的 **H1 标签支持密度** 与当前层允许考虑的 **有效空间覆盖规模** 共同决定。

直观规则是：

**如果扩大当前低层搜索范围后，预计已经能提供足够的匹配 H1 候选，就停止继续向上复制该 child-label；只有预计仍不足时，才允许它进入下一层。**

这避免要求“每个单独的 H2 posting 都必须装满目标候选数”。中等稀疏标签可以留在较低层，极稀疏标签则可以继续在高层聚合。
这是构建期的容量估计规则，不是查询时根据 selectivity 切换算法。

## 2. 总体结构与术语

| 对象 | 当前作用 |
|---|---|
| H1 | 原生 BKT/图导航层，节点对应已选出的物理 head；保留原图、向量及标签支持 |
| SSD posting 的 H 区域 | 标签约束的记录放置区域，供满足相应条件的过滤查询使用 |
| SSD posting 的 O 区域 | 原始空间放置区域；H 与 O 保持各自语义，存储为同一 posting 内的 H\|O |
| H2 | 成员是 H1 ID；通过标签子行索引相应 H1 成员 |
| H3–H5 | 成员严格为下一低层的 posting ID，不直接展开存储所有 H1 后代 |
| 上层 representative | 原始 H1 向量的物理 ID；不是额外保存的均值向量 |
| 上层临时 ANN 索引 | 仅服务构建中的原生空间分配和受限分配，不作为运行时上层 ANN 图 |
| 统计层次 | 由保留的全域源层次构建，只用于估计局部密度，不能与新查询 postings 混为一谈 |

命名上，**SSD 的 H/O 区域与 H1/H2/H3 层级不是同一个概念**。

每个上层节点保存 representative、层级、anchor label 和标签子行目录。一个物理节点可以承载多个标签子行；同一 child 对不同标签的分配可以不同。查询 OR 条件时合并相关子行并去重，而不是为每个标签分别启动完整搜索。

准入粒度是：

$$
(\text{当前 child 的物理 representative},\ \text{label},\ \text{层间转换})
$$

因此，不是“给整个标签指定一个全局终止层”，也不是“一个 posting 的所有标签同时升级”。
同一标签在不同区域可以有不同的向上准入结果。

## 3. 统计定义：什么叫局部稀疏

### 3.1 符号

| 符号 | 含义 |
|---|---|
| \(N\) | 原始记录总数 |
| \(N_1\) | 原始 H1 物理 head 总数；公式的规模上限是它，不是 \(N\) |
| \(S(h)\) | H1 head \(h\) 的完整受支持标签集合，包括已有 support expansion |
| \(\mathcal L\) | 全域 H1 支持标签目录 |
| \(\rho\) | 源索引的原生 `Ratio`，当前配置为 \(0.12\) |
| \(M\) | `HierarchyLocalTarget`，当前为 128 |
| \(B_0\) | `HierarchyLocalWindow`，当前为 4096 |
| \(W(u)\) | 统计节点 \(u\) 代表的不同 H1 数量 |
| \(A(u,\ell)\) | 这些 H1 中支持标签 \(\ell\) 的数量 |
| \(r(u)\) | 当前查询 posting 节点 \(u\) 的 H1 物理 representative |

这里的 \(M\) 是期望的 **匹配 H1 支持候选容量**，不是最终 top-k，也不是 `nprobe`。
同一 H1 可以支持多个标签，因此不同标签的 \(A(u,\ell)\) 之和可以大于 \(W(u)\)。

准入针对 `LimitedTagColumn` 对应的类别标签。它不直接估计任意多列 DNF 或数值条件的联合 selectivity。
一个 head 支持某标签，也不意味着它必然在某个查询的页数、距离和谓词限制内贡献一个独立的最终结果。

### 3.2 用唯一统计归属消除副本重复计数

统计来自 **重建前保留的、未按新准入规则筛选的全域相邻层 CSR**。
不能用新 H2/H3 已截断的少量运行时标签来估计密度，否则停止上升、未入选的区域会消失，分母和分子都会偏。

对源层次中每个 child \(v\)，从其已有父 posting 中选择唯一的统计 owner：

$$
\pi_j(v)=\operatorname{first}\bigl(\operatorname{Owners}_j(v)\bigr).
$$

当前具体实现选取规范 owner 顺序中的第一个。`PostingOwners` 按父 ID 递增建立这一顺序。
**这不是重新计算的最近父节点，也不能在论文中称为最近邻/Voronoi 归属。**
该选择只是确定统计分区；不改变真正的多副本 posting 放置。

H1 的初始统计为：

$$
W(h)=1,\qquad A(h,\ell)=\mathbf 1\{\ell\in S(h)\}.
$$

随后每层只合并紧邻低层的摘要：

$$
W(u)=\sum_{\pi_j(v)=u}W(v),
\qquad
A(u,\ell)=\sum_{\pi_j(v)=u}A(v,\ell).
$$

因为每个 child 只向一个统计 owner 转移质量，各层保持：

$$
\sum_{u\in \overline H_j}W(u)=N_1,
\qquad
\sum_{u\in \overline H_j}A(u,\ell)
=\sum_{h\in H_1}\mathbf 1\{\ell\in S(h)\}.
$$

\(\overline H_j\) 表示源统计层，不是新构建的查询层。
实现逐层检查总质量和每个标签计数守恒。
这里的去重针对 **统计质量**；实际查询 postings 仍允许一个 child 有多个父节点。

### 3.3 如何得到可扩大的局部窗口

当前实现先完成源 H1→H2→H3→H4→H5 的相邻摘要聚合，并用 ID 映射合成每个 H1 的唯一源 H5 统计地址 \(c(h)\)。
较高统计阶段不重新读取 H1 标签，不递归展开 H1 向量后代列表。

然后仅在 **源 H5 representatives** 上构建一棵原生二叉 BKT，即 k-means 分支数为 2 的小型统计树。
将 H5 统计单元的 \(W,A\) 聚合到这棵树的祖先节点，形成嵌套区域。

对统计单元 \(c\)，从代表该单元的 BKT 节点开始沿祖先链向上，选择能覆盖所需 H1 质量的第一个区域。后续层继续沿同一祖先链扩大窗口。
所选区域包含该 BKT 节点及其后代所代表的统计单元；起始节点如果是内部节点，其区域已经可能包含多个单元。

必须准确表述以下实现边界：

- 当前不是在每个新 H2 posting 周围实时做 kNN 密度搜索。
- 当前也不是直接以新 H2 的两个运行时标签推导 H3 密度。
- 先对完整源层次做一次相邻摘要传递，再使用共享的粗粒度空间区域，在构建新 H2–H5 时查准入表。
- 区域是由源空间关联和小型 BKT 得到的近似空间分区，不是以查询点为中心的精确 metric ball。
- 确定性首 owner、粗区域和 BKT 分组都可能影响局部性；不能宣称无偏抽样或给出未经证明的局部估计误差界。

## 4. 逐层准入公式

### 4.1 当前低层允许考虑多大的有效覆盖

令 \(k=1,2,3,4\) 对应 \(H_k\to H_{k+1}\)。
当前请求的有效 H1 覆盖质量为：

$$
\boxed{
B_k=\min\left(N_1,\left\lceil\frac{B_0}{\rho^{\,k-1}}\right\rceil\right).
}
$$

实现以 `long double` 逐次除以 \(\rho\)、取上界并做整数 `ceil`。
\(1/\rho\) 是来自构建比例的 **覆盖增长近似**，不是已测量的不同 H1 并集增长率。
\(B_k\) 也不是距离计算次数、读页次数或代码严格兑现的搜索范围。

记选中的统计区域为 \(R_k(h)\)。它是上述祖先链上满足

$$
W(R_k(h))\ge B_k
$$

的第一个区域，且各次所选区域嵌套。

### 4.2 局部支持密度与预计候选容量

定义：

$$
\widehat s_k(h,\ell)=
\frac{A(R_k(h),\ell)}{W(R_k(h))}.
$$

这是 **该区域内支持 \(\ell\) 的 H1 比例**，不是原始数据中具有 \(\ell\) 的记录比例。
预计在当前有效覆盖规模内得到的匹配候选容量是：

$$
\boxed{
\widehat C_k(h,\ell)
=\widehat s_k(h,\ell)B_k
=\frac{A(R_k(h),\ell)}{W(R_k(h))}B_k.
}
$$

一个重要修正是：**不能直接用整个粗区域的标签计数 \(A(R_k(h),\ell)\) 作为可访问容量。**
如果请求覆盖 4096 个 H1，但实际统计区域覆盖 30000 个 H1，多出来的区域只是密度样本，不代表查询可以免费搜索完这 30000 个 H1。
必须通过 \(B_k/W(R_k(h))\) 缩放计数。

### 4.3 准入与“停止后不重启”

为每个固定物理 representative 和标签建立四位准入掩码。令 \(a_0(h,\ell)=1\)，则：

$$
\boxed{
a_k(h,\ell)=
a_{k-1}(h,\ell)\,
\mathbf 1\!\left[
\widehat C_k(h,\ell)<M
\right].
}
$$

等价的无除法整数判断为：

$$
\boxed{
A(R_k(h),\ell)\,B_k
<
M\,W(R_k(h)).
}
$$

两个乘积按 `uint64` 计算。**严格小于才上升；等于目标就不继续上升。**
掩码只允许连续前缀：`0000`、`0001`、`0011`、`0111`、`1111`，低位对应进入 H2。

必须区分两种单调性：

- 嵌套统计区域的质量和标签总计数不会下降。
- 区域密度及缩放后的 \(\widehat C_k\) **不保证单调**，因为扩大统计区域可能稀释密度。

所以“停止后不重启”是实现中显式施加的准入策略，不能表述为由密度公式自动推出的定理。

### 4.4 公式如何作用于实际 child-label

对当前层节点 \(u\)，令 \(L(u)\) 为其实际承载的标签集合。本轮可以向上转移的标签为：

$$
L_k^{\uparrow}(u)
=\{\ell\in L(u):a_k(r(u),\ell)=1\}.
$$

H1 的起点是 \(L(h)=S(h)\)。更高层的 \(L(u)\) 来自已经构建好的标签子行。
仅当 \(L_k^{\uparrow}(u)\ne\varnothing\) 时，该物理 child 才进入下一层代表点选择的候选集合。

需要保留的细节：

- 判断的是 **child 的 representative 所在区域**，不是拟建 parent 的全局属性。
- 同一节点中的标签分别判断；一个稀疏标签不会自动把所有其他标签带上去。
- “停止”表示不再把该 child-label 作为下一层输入，**不会删除它已经存在于低层的 posting**。
- 更高层某标签可能从其他合格 child 聚合而来。因此不能仅凭 parent representative 的标签/准入状态，判断其整条标签子行是否合法；验证实际针对子行中的 child-label 成员。
- 一个 parent 的标签可以由其他 child 提供，其 representative 自己未必原本支持该标签。
- 若本轮没有任何合格 child，后续更高层不再构建。

所以准入表覆盖每个统计地址与完整标签目录 \(\mathcal L\) 的组合，而不是只为某个 representative 原始的 \(S(h)\) 建表。
不同层的 representative 可以改变；前缀约束针对固定统计地址/标签，不能推成所有携带同一标签的路径共享一个终止层。

### 4.5 对应局部 selectivity 的数量级

若 \(M=128,\ B_0=4096,\ \rho=0.12\)，且 \(N_1\) 尚未触发覆盖上限，则密度门槛为 \(\tau_k=M/B_k\)：

| 转换 | 请求有效 H1 覆盖 \(B_k\) | 局部支持密度门槛 \(\tau_k\) |
|---|---:|---:|
| H1→H2 | 4096 | 3.125% |
| H2→H3 | 34134 | 约 0.375% |
| H3→H4 | 284445 | 约 0.045% |
| H4→H5 | 2370371 | 约 0.0054% |

门槛针对各自区域的 \(\widehat s_k\)，并且需要此前各位准入成立。不是四个全局标签频率分桶。

如果一个标签的局部密度在各尺度近似稳定为 2%，则 H1 有效覆盖内预计有约 82 个候选，允许建立 H2；扩大低层覆盖到约 34134 个 H1 后预计有约 683 个候选，便不需要再向 H3 扩增。这正是“部分 H2 标签通过扩大低层范围即可满足需求”的容量建模方式。

若稳定为 0.2%，则前两次估计约为 8 和 68，继续进入 H3；第三次约为 569，停止进入 H4。
这些是说明公式的条件例子，不是对真实查询的访问数量保证。

### 4.6 与数据规模的关系

公式显式依赖 \(N_1\)、\(\rho\)、\(B_0\)、\(M\) 及空间支持分布。
当覆盖达到全体 H1 时：

$$
B_k=N_1,\qquad \tau_k=M/N_1.
$$

本轮 SIFT1M 的 \(N_1=119991\)，所以 H3→H4、H4→H5 的请求覆盖均截断为 119991，门槛均约为 **0.106675%**，不能套用上表未截断时的 0.045% 和 0.0054%。
若整个 H1 中某标签仅有 49 个支持 head，而 \(M=128\)，其容量即便扩大到全域仍不足，因而可以一路进入最高配置层 H5；这不意味着系统能凭空提供 128 个匹配 head。

## 5. 准入后如何实际构建上一层

局部公式只决定输入资格。后面的代表点选择、标签支持与 H/O 分配仍使用已有原生构建机制。

### 5.1 物理代表点数量与选择

令 \(U_k\) 为准入后仍有标签的不同物理 child 集合，\(C_{k+1}^{src}\) 为源对应上层的 head 数量上限。非空时：

$$
n_{k+1}
=\min\left(C_{k+1}^{src},
\max\left(1,\left\lceil\rho |U_k|\right\rceil\right)\right).
$$

配额计算只数不同物理 child，不数其副本，也不数展开后的多个 child-label。
在这些物理向量上建立原生 BKT，优先从尚未展开的、人口较大的空间子树选取原生中心，直到达到配额。
**不是每个标签单独建聚类或单独增配代表点。**

被选中的 representative 来自某个 child。其 anchor label 继承该 child 的有效 anchor；如果旧 anchor 已被本轮准入剔除，则换成其排序后的第一个剩余标签。

### 5.2 临时 O 分配与有限标签支持

在本轮 representatives 上建立共享的临时原生 ANN 索引。
先对每个合格物理 child 做一次不按标签限制的 O 候选搜索及原生 RNG 选择，再让该 child 的逻辑标签项共享这个空间候选结果。

每个 parent 的基础支持：

1. 保留自己的 anchor label。
2. 从保留的空间候选中取最近的 `slots-1` 个逻辑候选位置。
3. 对这些候选的标签去重，形成基础标签集合。

**重复标签仍占候选位置。** 不能改写为“一直向外找，直到凑齐指定数量的不同标签”。
这与 H1 基础支持中的最近不同外部标签选择不是同一条规则。

当前基础槽数为 2，但若源配置已启用 retained-O support expansion，会继续使用已有的最低支持覆盖扩展机制，目标受实际可用 O 来源限制。
因此不是“每个上层节点最终严格只有两个标签”，也不是无条件保证每个标签都有 16 个非空可查询 posting。
局部准入不修改这套既有支持扩展规则。

本阶段统计的标签人口是合格 **逻辑 child-label 项** 数量，用于上层支持构建；它不是第 3 节的全域 H1 统计 \(A\)，不能拿来替代局部密度分子。

### 5.3 受限 H 分配与副本

每个合格 child-label 独立执行受限候选搜索，只接受支持该标签的 parent，并沿用原生 H-posting RNG 规则。
对按 child 距离排序的候选 parent \(p\)，如果已有已选 parent \(q\) 满足：

$$
\lambda\, d(p,q)\le d(u,p),
$$

则拒绝 \(p\)。这里 \(\lambda\) 是原生 `RNGFactor`，当前距离为原生 L2 定义。

`HierarchyReplicaCount` 是最多保留多少副本，不是必须凑满的数量。
不能补齐被 RNG 拒绝的副本。所选代表点的 anchor 逻辑项使用一个显式 self-child。
只有受限搜索完全无落点时，才使用既有的、扫描匹配 parent 支持池的精确回退；仍无法放置会明确报错。

最后按 `(parent,label)` 形成排序、去重的相邻 child ID 子行。
临时 O 及 ANN 构建状态不作为新的运行时上层图保存；最终上层服务结构是这些标签化相邻 postings。
H2 构建还会为所有 H1 预计算一个空间 H2 入口，使用原生 ANN 而非每标签全局入口。

## 6. 简化伪代码

```text
输入：
    保留的 H1、完整支持集合 S、源全域相邻 CSR、源各层代表点
    原生参数 M, B0, rho，以及原有支持/副本/构建参数

统计阶段：
    W(h) = 1
    A(h, label) = 1[label in S(h)]
    对源 H1->H2->H3->H4->H5 的每个相邻转换：
        pi(child) = first canonical source owner
        每个 child 的 W/A 只向 pi(child) 合并一次
        检查总 H1 质量、各标签总支持量守恒
    合成 H1 -> 源 H5 统计地址 c(h)
    仅用源 H5 representatives 建二叉统计 BKT
    合并各 BKT 子树的 W/A
    对每个共享统计地址 c：
        node = BKT 中代表 c 的节点
        对完整标签目录中的每个 label，初始化 mask[0] = true
        对 k = 1..4：
            Bk = min(N1, ceil(B0 / rho^(k-1)))
            沿祖先链扩大 node，直到 W(node) >= Bk
            对每个 label：
                mask[k] = mask[k-1] AND
                          (A(node,label) * Bk < M * W(node))

查询层次构建阶段：
    lower = H1 节点及其完整支持标签
    对 k = 1..4：
        对每个 lower child：
            只保留 mask[c(representative),label,k] 为真的输入标签
        去掉本轮无剩余输入标签的 child；若为空则结束
        按不同物理 child 数量、rho、源层上限决定 parent 数量
        用共享原生 BKT 选择 representatives
        用临时原生 ANN 完成 O 候选、基础标签及既有支持扩展
        对每个 child-label 做原生受限 H/RNG 放置
        保存标签子行中的相邻 child ID
        lower = 新建 parent 及其实际标签集合
    校验相邻层、child-label 覆盖、副本上限和指纹
    建反向 owner 目录
```

统计阶段的扫描与逻辑标签循环都属于构建期。查询不会重新运行上述 mask 判定。

## 7. 查询流程：V5 没有新增动态 selectivity 导航器

先完整运行原生 H1 图搜索。普通非匹配节点仍可充当导航桥梁，谓词限制结果接纳。
若已获得足够 H1 结果，完全不执行 posting-owner、上层 signature、上层 representative 或 CSR 工作。

若 H1 不足、功能启用且标签适合该 posting 路径，则至多执行一次共享补充：

1. 从 H1 已实际评分的候选中取 anchors；`PostingAnchorCount=0` 使用当前 `nprobe` 作为 anchor 上限，允许已评分的负样本作为入口。
2. 合并 anchors 的反向 owners 和预存空间入口，按 query-to-representative 距离组织统一前沿。
3. 上下层共享该前沿。完成选中的标签子行后才检查相应工作预算；OR 标签共享去重与补充预算。
4. 保留原 H1 结果，只用补充候选填充或替换补充部分；不会恢复原图搜索，也不会因新补充 H1 而重新扫描全局支持表。
5. 最后进入原生 SSD 检索、原始 VID 处理、去重及精确类别/数值条件过滤。

V5 查询激活只使用 **实际保存的 H2 标签域**，不再用旧全局 selectivity 阈值判断该标签是否具有上层结构。
构建统计 payload 在加载校验后释放，不参与逐查询密度估计。

上层 signature 是保守 may-match，不等于记录满足最终谓词。
不匹配入口/owner 可以用于一次向上恢复，但从已选上层行下降遇到的负 child 不会无条件沿所有其他 owners 扩散。

查询仍有既有近似限制：

- H1 的原生 `MaxCheck`、补充预算 `PostingAdditionalMaxCheck` 和原有 H2 代表点收敛规则。
- 正数 `PostingNavigationWidth` 对不同逻辑上层分别施加代表点距离 beam；它不是硬性“最多访问这么多行”，距离相同的候选仍可通过。
- width 为 0 只关闭这个额外的上层 beam，不关闭其他收敛、预算和 SSD 页数限制。
- `SearchPostingPageLimit` 限制每个选中 SSD 区域的扫描范围。

因此，**较高层能聚合到相应标签，不等于任何有限预算查询都能读到其全部后代**。
本规则也不会在查询中强制执行“扩大到 \(B_k\) 个不同 H1”的动作；\(B_k\) 是准入模型的覆盖假设。

## 8. 额外开销与持久化

### 8.1 局部统计新增的工作

H1 支持标签读一遍；之后传递相邻摘要。
给定已有反向 owner 目录，四次相邻质量转移的 child 数为：

$$
\sum_{j=1}^{4}|\overline H_j|.
$$

此外有稀疏标签摘要合并、触及标签排序、统计地址合成、小型 BKT 构建及其节点上的标签统计聚合。
不能把总复杂度只写成上述 child 次数而漏掉标签摘要开销。
源反向 owner 目录自身的创建/加载也需处理已有副本引用，不能声称完全没有 CSR 副本读取成本。

局部统计本身不新增逐标签 ANN，不做 H1 全向量邻域搜索，不反复展开并扫描全部 H1 向量后代。
但**完整上层重建仍有原生代表点选择、ANN/RNG 分配、H2 全 H1 空间入口分配和受限搜索为空时的精确回退**。
更多 child-label 被准入会增加这些既有阶段的成本，不能写成“整个算法不再做向量扫描或 ANN”。

### 8.2 构建统计与运行时内存

密度阶段需要 H1 地址数组、相邻两层稀疏摘要及桶分配暂存，还需要粗 BKT 节点的标签矩阵。
若粗树节点数为 \(T\)、源 H5 单元数为 \(n_5\)、标签数为 \(L\)，粗矩阵为 \(O(TL)\)，共享 mask 为 \(O(n_5L)\)，地址为 \(O(N_1)\)。

V5 在原相邻 posting 文件后附加：

- H1→统计区域的 `uint32` 地址。
- 排序标签目录。
- 每个区域四个实际统计窗口质量。
- 每个区域/标签一个 byte，保存四位准入 mask。

当前附加 payload 字节数是：

$$
32+4N_1+4L+16n_5+n_5L.
$$

以当前 SIFT1B 源的 \(N_1=120040156,\ n_5=24994,\ L=201\) 计算，约为 485.6 MB，即 463.1 MiB。
这只是该附录的尺寸，不是整个索引、构建峰值内存或常驻查询额外内存。
加载时校验局部决定、成员覆盖和指纹，之后释放这些构建统计，仅保留实际 H2 标签域及必要指纹等服务状态。
旧源全域上层也不是新 V5 查询所必需的并行运行时图。

## 9. 已确认的调试事实与论文使用边界

### 9.1 当前状态

V5 已在真实 SIFT1M 上完成构建及正确性调试。SIFT1B 上层重建已经启动；本文编写时尚未发布完成文件。
不能把 SIFT1M 数字当作 SIFT1B 的已完成实验，也不能声称 V5 已取得 SIFT1B recall/QPS 改善。

本轮 SIFT1M 的局部 census 为 136326 次相邻 child 转移，耗时日志为 0.024 秒，`peakSummaryBytes=4028628`，粗向量数为 25。
这里的内存值只统计代码中的相邻摘要容量，不是进程 RSS；时间也不能直接外推为完整重建时间或论文级跨规模结论。

| SIFT1M 结构 | V4 全局准入 | V5 局部准入 |
|---|---:|---:|
| H2 物理 postings | 9786 | 10994 |
| H3 物理 postings | 147 | 574 |
| H4 物理 postings | 0 | 2 |
| H5 物理 postings | 0 | 1 |
| 稀疏 tag200 的最高承载层 | H3 | H5 |
| tag200 的不同 H1 支持成员 | 49 | 49 |

tag200 的原始记录数是 193；上述 49 是支持它的 H1 数量，两者不能混用。

### 9.2 正确的 SIFT1M 查询配置

本轮最终比较保留 `MaxCheck=2048`、额外补充 2048、anchor 数随 `nprobe`、`PostingNavigationWidth=8`。
SIFT1M 为 128 维 Float、两个属性，每条记录约 524 bytes；应使用原 SIFT1M 配置的 **12 个搜索页**。
最初误用 3 页导致高 `nprobe` recall 约 0.74，已定位为扫描预算配置错误并修正，不是准入模型的召回上限。
不能机械地把 UInt8 数据的页数照搬到 Float，而忽略每页记录数。

在正确的 12 页、width=8 配置下：

| nprobe | 稀疏 V4 | 稀疏 V5 | 混合 DNF V4 | 混合 DNF V5 |
|---|---:|---:|---:|---:|
| 48 | 0.9916 | 0.9919 | 0.9732 | 0.9712 |
| 96 | 0.9996 | 0.9999 | 0.9943 | 0.9948 |
| 192 | 0.9999 | 0.9999 | 0.9978 | 0.9976 |
| 384 | 0.9999 | 0.9999 | 0.9980 | 0.9990 |

其余中等、稠密、无过滤场景的对应输出不变；过滤、原 H1 保留和普通/诊断版本结果一致性已检查。
混合查询的两个小幅回退已经由用户明确接受后放行 SIFT1B 重建，原自动“逐点不回退”拒绝报告仍保留。
**不能写成全部场景、全部点均有提升或自动通过零回退验收。**
这些是调试证据，不是可以直接发布的论文 QPS。

### 9.3 建议的论文组织与禁用表述

方法部分可以按“共享空间 H1 基础 → 有限标签相邻 postings → 守恒局部统计 → 预计候选容量准入 → 不改变 H1-first 的补充查询”组织。
公式的主线应是 \(\widehat C_k=\widehat s_kB_k\) 与不足目标才上升，而不是四个经验全局 selectivity 分桶。

避免以下失真：

| 不应写成 | 准确表达 |
|---|---|
| 每个标签只属于一个固定层 | 准入按空间区域、child representative 和标签逐层决定 |
| 上层密度由当前节点选中的两个标签估计 | 来自完整源支持的相邻守恒摘要 |
| 统计 owner 是最近父节点 | 当前是已有规范 owner 顺序中的首个父节点 |
| 每个 posting 必须有至少 128 个匹配成员 | 128 是扩大低层有效覆盖后的预计候选目标 |
| 单层密度必然随上升降低/候选容量必然增加 | 估计可以变化，停止不重启由显式前缀策略保证 |
| 更高层直接保存所有 H1 后代 | 高于 H2 的成员严格是相邻低层 posting ID |
| 每层最多两个标签、恰好八副本 | 基础槽数为二，已有支持扩展可增加标签；八是副本上限 |
| 构建完全没有 ANN 或全向量读取 | 新增局部统计不做逐标签 ANN/全 H1 向量邻域扫描，原生分配仍存在 |
| 查询按局部 selectivity 自适应选择层级 | 准入在构建时固定，查询仍按统一前沿、signature 和原生预算运行 |
| 聚合到 H5 保证任意查询达到目标 recall | 聚合是结构属性，实际可达性还受入口、beam、工作预算和 SSD 页数影响 |
| 当前公式是概率置信界或召回下界 | 当前是确定性摘要支持的启发式候选容量估计 |

## 10. 代码与证据入口

下列代码路径相对 `SPTAG/`；行号对应本交接版本。

| 内容 | 入口 |
|---|---|
| 相邻统计、首 owner、守恒检查 | `AnnService/inc/Core/SPANN/LocalLabelCensus.h:15-123` |
| 源 H5 二叉统计 BKT、窗口、整数准入与 mask | `AnnService/inc/Core/SPANN/LocalLabelCensus.h:125-212` |
| mask 合法性、附录存取、释放 | `AnnService/inc/Core/SPANN/LocalLabelAdmission.h` |
| child-label 筛选、物理配额、逐层 H/O 构建 | `AnnService/inc/Core/SPANN/LayeredLabelHierarchyBuilder.h:34-288` |
| 人口优先原生代表点选择 | `AnnService/inc/Core/SPANN/SparseLabelHierarchyBuilder.h:52-106`，注意本文件后面的旧构建函数不是 V5 主路径 |
| 原生 RNG 与重复标签占候选位 | `AnnService/inc/Core/SPANN/RetainedOriginalPostings.h:13-70` |
| 相邻布局、标签目录、局部覆盖校验、反向 owners | `AnnService/inc/Core/SPANN/SparseLabelHierarchy.h` |
| 构建入口、加载后释放、查询标签域接入 | `AnnService/src/Core/SPANN/SPANNIndex.cpp` 的 `RebuildSparseHierarchy`、`ReleaseLocalStatistics`、`HasQueryLabel` 调用 |
| 前沿与独立上层距离 beam | `AnnService/inc/Core/SPANN/PostingNavigation.h:390-458` |
| SIFT1B 准入参数 | `Tools/benchmarks/rebuild_sift1b_local_label_hierarchy_v5.ini` |
| 正确的 SIFT1M 验证参数 | `Tools/benchmarks/validate_sift1m_local_label_hierarchy_v5.ini` |

本机工作区根目录为 `/mnt/nvme/baotonglu/mocheng`。调试证据：

```text
datasets/sift1m_zipf200_sparse193_numeric/build_runs/
    local_admission_v5_source/
    local_label_hierarchy_v4_control/
    local_label_hierarchy_v5/

datasets/sift1m_zipf200_sparse193_numeric/comparisons/local_admission_v5/
    run_20261004T030425Z/acceptance.json
    run_20261004T030425Z/acceptance.user-approved.json

datasets/sift1b/build_runs/local_label_hierarchy_v5/
    native-rebuild.ini
    protected-before.json
    build-native.log
```

SIFT1M 两个上层目录下的 `hierarchy-audit.json` 给出逐层、逐标签子行与引用数；`build-completion.json` 给出构建元数据。
SIFT1B 是否真正完成，应以其后续产生且通过控制器检查的 `build-completion.json` 为准，不能仅因目录存在就视为完成。
