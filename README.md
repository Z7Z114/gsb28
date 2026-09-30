# ffs-pad —— ARM64EC 快速转发桩（FFS）landing pad 修补工具

Madeira 在非越狱 iPhone 上跑 Windows 游戏时，x86-64 侧的 hook 引擎（反作弊、覆盖层、
性能工具）会去 hook 系统 DLL 的导出入口。微软的 ARM64EC 镜像里，一个导出入口是一段
16 字节的 x86-64「快速转发序列」（fast-forward sequence，FFS）：

```
48 8b c4           mov  rax, rsp
48 89 58 20        mov  [rax+20h], rbx
55 5d              push rbp / pop rbp
e9 <rel32>         jmp  <ARM64EC 实现>
cc cc              int3 / int3
```

在 x64 Windows 上，这个 jmp 后面是 x64 转发桩，引擎顺着它走没问题；在 ARM64EC 上它落到
ARM64 指令里 —— 引擎于是把 x64 分支写进 ARM64 代码，原生调用者执行这些字节就 SIGILL。

本工具给每个 FFS 在模块自己的 `.hexpthk` 段尾部补一个 landing pad。`.hexpthk` 本身已经是
CODE|EXEC|READ，而且 `SizeOfRawData` 远大于 `VirtualSize`，多出来的部分是零填充的 slack：

```
pad:  90 x19          <- 每个字节都是指令边界
      e9 <rel32>      jmp <ARM64EC 实现>
```

FFS 的 jmp 改指到这个 pad。于是 hook 引擎顺着 jmp 落到 pad、在原地改写 x64 字节，而原生
ARM64 调用者不受影响：它们通过镜像的 RedirectionMetadata（FFS rva → 实现 rva）在字节
解码之前就拿到了实现。

## 用法

```
python tools/ec-ffs-pad.py <arm64ec dll> [<dll> ...]
```

镜像就地改写。每个文件打印一行：

```
  OK     <文件名>   <n> thunks padded, pads at rva <起点>..<终点>, .hexpthk VirtualSize <旧> -> <新>
  SKIP   <文件名>   <原因>
  FAIL   <文件名>   <原因>
```

最后一行是汇总：`<n> padded, <m> skipped, <k> FAILED`。只要有一个 FAIL，退出码为 1，
否则为 0；无参数时打印用法并以退出码 2 结束。`samples/` 下有几个 ARM64EC 镜像样本，
用来在没有真机的情况下自测。工具只用 Python 标准库，不联网、不依赖任何第三方包。

## 行为规格（验收标准）

下面每一条都是验收点。**SKIP 一律表示「这份镜像不动它」：判定为 SKIP 时文件必须一个
字节都不变。**

- **S1 命令行**：见上面的用法、状态行与汇总行；无参数 → stderr 打印用法、退出码 2；
  SKIP 不算失败（退出码仍为 0），只有 FAIL 才让退出码变 1。
- **S2 镜像识别**：不是 PE32+（没有 MZ / PE 签名，或文件读不出来）→ SKIP。没有
  `.hexpthk` 段 → SKIP（不是 ARM64EC 镜像）。
- **S3 thunk 阵列**：从 `.hexpthk` 的段内偏移 0 开始按 16 字节步长枚举；只有「9 字节
  canonical 序言 `48 8b c4 48 89 58 20 55 5d` 后面跟一个 `e9`」才算一个 FFS thunk，
  遇到第一个不符合的条目就停止枚举。数出 n 个之后，`.hexpthk` 的 `VirtualSize` 必须
  正好等于 `n * 16`；不等说明这份镜像的布局不是本工具理解的那种（例如已经打过 pad，
  或者换了编译器）→ SKIP，不许猜着改。
- **S4 pad 区大小**：pad 区从 `.hexpthk` 的 `VirtualSize` 向上取整到 16 的倍数开始
  （段内偏移），每个 pad 24 字节，n 个共 `24 * n` 字节；`SizeOfRawData` 在这个起点之后
  剩下的空间不够 → SKIP。
- **S5 slack 必须是零填充**：要写入的那个 `24 * n` 字节区间里只要出现一个非零字节，
  就说明那已经不是零填充的 slack（可能是别人的数据）→ SKIP，不许覆盖。
- **S6 RedirectionMetadata 定位**：可选头的 DataDirectory[10] 给出 load config；在它的
  前 min(Size, 0x200) 字节里按 8 字节步长扫描 8 字节槽。一个槽的值 v 算合格候选，当且
  仅当：v 落在 `[ImageBase, ImageBase + SizeOfImage)` 内；`v - ImageBase` 能映射到文件；
  目标结构的 `+0x00`（Version）是 1 或 2；`+0x10`（RedirectionMetadata rva）非 0；
  `+0x34`（RedirectionMetadataCount）**等于实测的 thunk 数 n**；表项的 rva 范围在文件内。
  取扫描顺序里第一个合格候选。找不到 → SKIP。
- **S7 覆盖性**：每一个 thunk 的 RVA（`.hexpthk` 的 RVA 加上 `i * 16`）都必须出现在
  RedirectionMetadata 表里（表项的第一个 dword 是 Source rva）。只要有一个 thunk 不在表
  里，就 SKIP 整份镜像：这种镜像的原生调用者要依赖字节解码，给它打 pad 会打断原生路径。
- **S8 pad 内容**：pad 起点起的 19 个字节必须是单字节 NOP `90`（`PAD_SIZE - 5`；这样
  hook 引擎从 pad + N 恢复执行时，任何 N ≤ 19 都落在指令边界上），第 20 个字节是 `e9`，
  紧接着 4 字节小端 rel32。rel32 的基准是 **jmp 之后的下一条指令**，也就是 pad 起点 +
  24；目标是这个 thunk 改写前指向的 ARM64 实现 RVA（改写前 `thunk起点 + 14 + rel32`）。
- **S9 thunk 改写**：thunk 自己那 5 字节 jmp（偏移 +9..+13）改成指向它自己的 pad 首字节，
  即 rel32 = `pad 起点 rva - (thunk 起点 rva + 14)`。
- **S10 段头**：`.hexpthk` 的 `VirtualSize` 必须扩到刚好覆盖全部 pad，即
  `pad区起点 + 24 * n`。pad 落在 `VirtualSize` 之外时，PE 映射和 JIT 池里的副本都会按旧
  长度处理，pad 根本不存在。
- **S11 幂等**：对已经打过 pad 的镜像再跑一次必须 SKIP（S3 的长度检查会挡住），第二次
  运行不得再改动文件。
- **S12 最小改动**：文件长度不变。除「每个 thunk 的 rel32 四个字节」「pad 区的
  `24 * n` 字节」「`.hexpthk` 段头的 VirtualSize 四个字节」之外，任何字节都不得改变。
- **S13 多文件**：一次传多个文件时逐个独立处理，互不影响；退出码按 S1 汇总。
- **S14 写后自检**：写出后必须重新读回磁盘上的文件，逐 thunk 验证
  `FFS -> pad -> jmp -> 原来那个实现` 这条链；任何一条链断了就把该文件标为 FAIL
  （退出码 1），不许报 OK。

## 已知问题

真实使用中暴露出来的现象，不完整，只是举例（验收以「行为规格」为准）：

1. 有些镜像打完以后，hook 引擎顺着 FFS 的 jmp 进去，执行没几个字节就跳飞 —— 落点像是
   实现入口前面几条指令的中间。
2. 同一份镜像在一台机器上说「找不到能用的 RedirectionMetadata」被拒绝，换一份就又好了。
3. 打完的镜像里，`.hexpthk` 的 `VirtualSize` 看着像只覆盖了第一个 pad。
4. 同一个文件跑第二遍，工具会再打一次 pad，而不是拒绝。
