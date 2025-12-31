# aiwen/models/mes/indicator.py
from __future__ import annotations  # 支持前向引用

from datetime import datetime

from sqlalchemy import DateTime, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from aiwen.extensions.database import get_base

Base = get_base("mes")


class AiIndicatorDimensionInfo(Base):
    __tablename__ = 'ai_indicator_dimension_info'
    __table_args__ = {'comment': '指标维度'}

    # ========== 主键 & 审计字段 ==========
    gid: Mapped[str] = mapped_column(String(32), primary_key=True, server_default='', comment='主键')
    create_id: Mapped[str] = mapped_column(String(32), server_default='', comment='创建人')
    create_date: Mapped[datetime] = mapped_column(DateTime, comment='创建时间')
    modify_id: Mapped[str | None] = mapped_column(String(32), comment='修改人')
    modify_date: Mapped[datetime | None] = mapped_column(DateTime, comment='修改日期')
    is_delete: Mapped[int] = mapped_column(SmallInteger, server_default='0', comment='删除标识(0未删除1删除)')
    is_active_raw: Mapped[int] = mapped_column('IS_ACTIVE', SmallInteger, server_default='0', comment='激活标识(0激活1冻结)')

    # ========== 多租户字段 ==========
    data_role: Mapped[str] = mapped_column(String(32), server_default='-1', comment='工厂GID')
    data_role1: Mapped[str | None] = mapped_column(String(32), comment='工作中心 GID')
    data_role2: Mapped[str | None] = mapped_column(String(32), comment='工作中心层级权值')

    # ========== 业务字段 ==========
    indicator_id: Mapped[str | None] = mapped_column(String(32), comment='指标')
    dimension_code: Mapped[str | None] = mapped_column(String(200), comment='维度编码')
    dimension_name: Mapped[str | None] = mapped_column(String(200), comment='维度名称')
    sql_snippet: Mapped[str | None] = mapped_column(String(500), comment='SQL片段')
    sql_snippet_default: Mapped[str | None] = mapped_column(String(500), comment='SQL片段默认值')

    # ========== 预留字段（UDA）==========
    uda1: Mapped[str] = mapped_column(String(50), server_default='')
    uda2: Mapped[str] = mapped_column(String(50), server_default='')
    uda3: Mapped[str] = mapped_column(String(50), server_default='')
    uda4: Mapped[str] = mapped_column(String(100), server_default='')
    uda5: Mapped[str] = mapped_column(String(100), server_default='')

    @property
    def is_enabled(self) -> bool:
        return self.is_active_raw == 0


class AiIndicatorCategory(Base):
    __tablename__ = 'ai_indicator_category'
    __table_args__ = {'comment': '北极星指标分类'}

    gid: Mapped[str] = mapped_column(String(32), primary_key=True, server_default='', comment='主键')
    create_id: Mapped[str] = mapped_column(String(32), server_default='', comment='创建人')
    create_date: Mapped[datetime] = mapped_column(DateTime, comment='创建时间')
    modify_id: Mapped[str | None] = mapped_column(String(32), comment='修改人')
    modify_date: Mapped[datetime | None] = mapped_column(DateTime, comment='修改日期')
    is_delete: Mapped[int] = mapped_column(SmallInteger, server_default='0', comment='删除标识(0未删除1删除)')
    is_active_raw: Mapped[int] = mapped_column('IS_ACTIVE', SmallInteger, server_default='0', comment='激活标识(0激活1冻结)')

    data_role: Mapped[str] = mapped_column(String(32), server_default='-1', comment='工厂GID')
    data_role1: Mapped[str | None] = mapped_column(String(32), comment='工作中心 GID')
    data_role2: Mapped[str | None] = mapped_column(String(32), comment='工作中心层级权值')

    name: Mapped[str | None] = mapped_column(String(200), comment='分类名称')
    description: Mapped[str | None] = mapped_column(String(200), comment='分类描述')
    pid: Mapped[str | None] = mapped_column(String(32), comment='上级分类')
    sort: Mapped[str | None] = mapped_column(String(200), comment='排序字段')

    uda1: Mapped[str] = mapped_column(String(50), server_default='')
    uda2: Mapped[str] = mapped_column(String(50), server_default='')
    uda3: Mapped[str] = mapped_column(String(50), server_default='')

    @property
    def is_enabled(self) -> bool:
        return self.is_active_raw == 0


class AiIndicatorInfo(Base):
    __tablename__ = 'ai_indicator_info'
    __table_args__ = {'comment': '指标基础信息配置'}

    gid: Mapped[str] = mapped_column(String(32), primary_key=True, server_default='', comment='主键')
    create_id: Mapped[str] = mapped_column(String(32), server_default='', comment='创建人')
    create_date: Mapped[datetime] = mapped_column(DateTime, comment='创建时间')
    modify_id: Mapped[str | None] = mapped_column(String(32), comment='修改人')
    modify_date: Mapped[datetime | None] = mapped_column(DateTime, comment='修改日期')
    is_delete: Mapped[int] = mapped_column(SmallInteger, server_default='0', comment='删除标识(0未删除1删除)')
    is_active_raw: Mapped[int] = mapped_column('IS_ACTIVE', SmallInteger, server_default='0', comment='激活标识(0激活1冻结)')

    data_role: Mapped[str] = mapped_column(String(32), server_default='-1', comment='工厂GID')
    data_role1: Mapped[str | None] = mapped_column(String(32), comment='工作中心 GID')
    data_role2: Mapped[str | None] = mapped_column(String(32), comment='工作中心层级权值')

    name: Mapped[str | None] = mapped_column(String(200), comment='指标名称')
    description: Mapped[str | None] = mapped_column(String(500), comment='描述')
    type: Mapped[int | None] = mapped_column(SmallInteger, comment='类型: 0-普通,1-原子,2-复合,9-SQL')
    expression: Mapped[str | None] = mapped_column(String(200), comment='表达式')
    is_enable: Mapped[int | None] = mapped_column(SmallInteger, comment='是否启用 (1=启用)')
    sql_text: Mapped[str | None] = mapped_column(String(500), comment='SQL')
    judge_mode: Mapped[int | None] = mapped_column(SmallInteger, comment='异常判定: 0-阈值,1-动态值')

    alarm_upper: Mapped[str | None] = mapped_column(String(100), comment='报警上限')
    alarm_lower: Mapped[str | None] = mapped_column(String(100), comment='报警下限')
    warning_upper: Mapped[str | None] = mapped_column(String(100), comment='预警上限')
    warning_lower: Mapped[str | None] = mapped_column(String(100), comment='预警下限')

    trend: Mapped[int | None] = mapped_column(SmallInteger, comment='动态判别: 0-平均,1-中位')
    trend_compare: Mapped[int | None] = mapped_column(SmallInteger, comment='比较方式: 0-不应高于,1-不应低于')

    category_id: Mapped[str | None] = mapped_column(String(32), comment='北极星指标分类')

    uda1: Mapped[str] = mapped_column(String(50), server_default='')
    uda2: Mapped[str] = mapped_column(String(50), server_default='')
    uda3: Mapped[str] = mapped_column(String(50), server_default='')

    @property
    def is_enabled(self) -> bool:
        return self.is_enable == 1


class AiDictDimension(Base):
    __tablename__ = 'ai_dict_dimension'
    __table_args__ = {'comment': '维度字段'}

    gid: Mapped[str] = mapped_column(String(32), primary_key=True, server_default='', comment='主键')
    create_id: Mapped[str] = mapped_column(String(32), server_default='', comment='创建人')
    create_date: Mapped[datetime] = mapped_column(DateTime, comment='创建时间')
    modify_id: Mapped[str | None] = mapped_column(String(32), comment='修改人')
    modify_date: Mapped[datetime | None] = mapped_column(DateTime, comment='修改日期')
    is_delete: Mapped[int] = mapped_column(SmallInteger, server_default='0', comment='删除标识(0未删除1删除)')
    is_active_raw: Mapped[int] = mapped_column('IS_ACTIVE', SmallInteger, server_default='0', comment='激活标识(0激活1冻结)')

    data_role: Mapped[str] = mapped_column(String(32), server_default='-1', comment='工厂GID')
    data_role1: Mapped[str | None] = mapped_column(String(32), comment='工作中心 GID')
    data_role2: Mapped[str | None] = mapped_column(String(32), comment='工作中心层级权值')

    dimension_code: Mapped[str | None] = mapped_column(String(200), comment='维度编码')
    dimension_name: Mapped[str | None] = mapped_column(String(200), comment='维度名称')
    dimension_description: Mapped[str | None] = mapped_column(String(500), comment='维度描述')
    component_id: Mapped[str | None] = mapped_column(String(100), comment='档案组件Id')

    uda1: Mapped[str] = mapped_column(String(50), server_default='')
    uda2: Mapped[str] = mapped_column(String(50), server_default='')
    uda3: Mapped[str] = mapped_column(String(50), server_default='')

    @property
    def is_enabled(self) -> bool:
        return self.is_active_raw == 0


class AiIndicatorRelation(Base):
    __tablename__ = 'ai_r_indicator'
    __table_args__ = {'comment': '指标关联关系表'}

    gid: Mapped[str] = mapped_column(String(32), primary_key=True, server_default='', comment='主键')
    create_id: Mapped[str] = mapped_column(String(32), server_default='', comment='创建人')
    create_date: Mapped[datetime] = mapped_column(DateTime, comment='创建时间')
    modify_id: Mapped[str | None] = mapped_column(String(32), comment='修改人')
    modify_date: Mapped[datetime | None] = mapped_column(DateTime, comment='修改日期')
    is_delete: Mapped[int] = mapped_column(SmallInteger, server_default='0', comment='删除标识(0未删除1删除)')
    is_active_raw: Mapped[int] = mapped_column('IS_ACTIVE', SmallInteger, server_default='0', comment='激活标识(0激活1冻结)')

    data_role: Mapped[str] = mapped_column(String(32), server_default='-1', comment='工厂GID')
    data_role1: Mapped[str | None] = mapped_column(String(32), comment='工作中心 GID')
    data_role2: Mapped[str | None] = mapped_column(String(32), comment='工作中心层级权值')

    p_indicator_id: Mapped[str | None] = mapped_column(String(32), comment='上级指标')
    sub_indicator_id: Mapped[str | None] = mapped_column(String(32), comment='子级指标')
    if_auto: Mapped[int | None] = mapped_column(SmallInteger, comment='是否自动分析 (1=是)')
    sort: Mapped[str | None] = mapped_column(String(100), comment='排序字段')

    uda1: Mapped[str] = mapped_column(String(50), server_default='')
    uda2: Mapped[str] = mapped_column(String(50), server_default='')
    uda3: Mapped[str] = mapped_column(String(50), server_default='')

    @property
    def is_auto(self) -> bool:
        return self.if_auto == 1
