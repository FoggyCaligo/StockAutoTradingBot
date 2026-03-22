data = {}

def calc_equilibrium(hoga_data):
    predicted_price = 0
    # 매도호가와 매수호가의 가중평균을 계산하여 예상 체결 가격을 구하는 로직
    
    return predicted_price
    
def add_data(stock_code, hoga_data):
    if stock_code not in data:
        data[stock_code] = {}
        data[stock_code]['hoga_data'] = [hoga_data]
        data[stock_code]['predicted_price'] = calc_equilibrium(hoga_data)
        data[stock_code]['predicted_revenue'] = 0