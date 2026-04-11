import talib
from rqalpha.api import *

def init(context):
    context.s1 = "000001.XSHE"
    
    # 沪深300指数（000300.XSHG）是在2005年4月8日才正式发布的
    
    # 1. 订阅股票（RQAlpha 6.x 只接受一个参数）
    subscribe(context.s1)
    
    # MACD 参数
    context.SHORTPERIOD = 12
    context.LONGPERIOD = 26
    context.SMOOTHPERIOD = 9
    context.OBSERVATION = 200  # 需要足够的历史数据
    
    context.slippage = 0.0001    # 设置滑点
    context.commission = 0.0002  # 设置手续费率
    logger.info("策略初始化完成，使用 1m 频率模拟 60m 逻辑")

def handle_bar(context, bar_dict):
    # 2. 时间过滤：模拟 60分钟 K线
    # A股交易时间：9:30-11:30, 13:00-15:00
    # 60分钟K线闭合时间点：10:30, 11:30, 14:00, 15:00
    current_time = context.now.strftime("%H:%M")
    
    # 只在这些时间点计算，其他时间（如 10:01, 10:02...）直接跳过
    if current_time not in ["10:30", "11:30", "14:00", "15:00"]:
        return

    # 3. 获取 1分钟历史数据（因为回测频率是 1m）
    # 注意：这里用 '1m'，因为我们在 1m 频率下回测
    prices = history_bars(context.s1, context.OBSERVATION, '1m', 'close')
    
    if len(prices) < context.OBSERVATION:
        return

    # 4. 计算 MACD
    macd, signal, hist = talib.MACD(prices, context.SHORTPERIOD, 
                                    context.LONGPERIOD, context.SMOOTHPERIOD)
    
    macd_now = macd[-1]
    signal_now = signal[-1]
    macd_prev = macd[-2]
    signal_prev = signal[-2]
    
    # 5. 交易逻辑
    # --- 死叉 ---
    if macd_now < signal_now and macd_prev >= signal_prev:
        logger.info("时间: {} | 60m级别死叉 (SELL)".format(current_time))
        curPosition = context.portfolio.positions[context.s1].quantity
        if curPosition > 0:
            order_target_value(context.s1, 0)
    
    # --- 金叉 ---
    elif macd_now > signal_now and macd_prev <= signal_prev:
        logger.info("时间: {} | 60m级别金叉 (BUY)".format(current_time))
        order_target_percent(context.s1, 1)

    # 6. 绘图
    plot("MACD", macd_now)
    plot("Signal", signal_now)