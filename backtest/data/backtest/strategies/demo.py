# RQAlpha 默认策略示例
from rqalpha.api import *

def init(context):
    # 选择一个股票（平安银行）
    context.s1 = "000001.XSHE"

def handle_bar(context, bar_dict):
    # 如果当前没有持仓
    position = context.portfolio.positions[context.s1]

    if position.quantity == 0:
        # 用全部资金买入
        order_percent(context.s1, 1.0)
