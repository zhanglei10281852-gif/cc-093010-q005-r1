"""全球产品资料本地化档案领域。

档案绑定产品、目标地区、合作机构与本地预期用途，按版本汇集来源证据与
翻译、术语校准、临床场景差异，经过医学、合规、运营三段串行审阅后发布。
"""

from app.localization.service import LocalizationService

__all__ = ["LocalizationService"]
