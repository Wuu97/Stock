from decimal import Decimal
from quant_core.portfolio import PortfolioPolicy, construct_buys
from quant_core.models import FeeModel
from quant_core.strategy import Recommendation

FEE=FeeModel('x',Decimal('0'),Decimal('0'),Decimal('0'),Decimal('0'),Decimal('0'))
def picks(price): return [Recommendation('A',1,Decimal('1'),Decimal(price),{})]
def test_fixed_target_notional_rounds_down_and_slots():
 p=PortfolioPolicy.fixed_target_notional(Decimal('200000'),5)
 assert construct_buys(picks('100'),p,Decimal('1000000'),FEE)[0].shares==2000
 assert construct_buys(picks('333'),p,Decimal('1000000'),FEE)[0].shares==600
 assert construct_buys(picks('2001'),p,Decimal('1000000'),FEE)==()
 assert construct_buys(picks('333'),p,Decimal('1000000'),FEE)[0].shares==construct_buys(picks('333'),p,Decimal('500000'),FEE)[0].shares==600
 assert construct_buys(picks('100'),p,Decimal('1'),FEE,occupied_positions=5)==()
 for price in ('100','333','2001'):
  for x in construct_buys(picks(price),p,Decimal('1'),FEE): assert x.shares%100==0 and x.shares*Decimal(price)<=Decimal('200000')

def test_fixed_target_notional_consumes_cash_in_rank_order():
 p=PortfolioPolicy.fixed_target_notional(Decimal('200000'),5)
 rs=[Recommendation('A',1,0,Decimal('100'),{}),Recommendation('B',2,0,Decimal('100'),{})]
 assert [x.ticker for x in construct_buys(rs,p,Decimal('300000'),FEE)]==['A']
 both=construct_buys(rs,p,Decimal('400000'),FEE)
 assert [x.ticker for x in both]==['A','B'] and sum(x.estimated_cash for x in both)<=Decimal('400000')
