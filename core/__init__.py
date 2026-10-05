"""收藏夹自动分类技能 —— 平台无关核心层。

分层：
  model / text / safety      纯工具，无依赖
  scoring / rules            分类算法，只认统一数据模型
  taxonomy / classify / plan / apply / report   流水线各阶段
  adapters/                  平台适配器（唯一知道具体平台细节的地方）

换平台只需要实现 adapters.Adapter 协议，core/ 其余部分不用改。
详见 references/porting-guide.md。
"""

__version__ = "1.0.0"
