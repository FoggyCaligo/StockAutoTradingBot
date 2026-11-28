"""Synthetic KRX-style ordinary-share call auction and volatility interruption.

Maximum executable volume, then minimum imbalance, then nearest last price.
The last tie rule is an explicit approximation of exchange edge cases.
"""
from collections import deque
import random


class AuctionRules:
    def __init__(self, book, *, seed=0, kospi200=False, jitter=30):
        self.book=book
        self.mode='continuous'
        self.now=0.
        self.until=0.
        self.static_reference=book.last_price
        self.dynamic_rate=.03 if kospi200 else .06
        self.closing_rate=.02 if kospi200 else .04
        self.static_rate=.10
        self.rng=random.Random(seed)
        self.jitter=jitter
        self.markets={'buy':deque(),'sell':deque()}
        self.protected=False
        self.events=[]
        self.tick_events=[]
        self.auction_price=0
        self.auction_volume=0

    def event(self,event_type,**data):
        item=dict(time_seconds=self.now,event=event_type,**data)
        self.events.append(item);self.tick_events.append(item)

    def begin_tick(self,seconds):
        self.now=seconds;self.tick_events=[];self.auction_price=0;self.auction_volume=0
        if seconds>=22800 and self.mode=='continuous':
            self.mode='closing_auction';self.until=23400+self.rng.uniform(0,self.jitter)
            self.event('closing_auction_start',until=self.until)
        elif seconds>=22800 and self.mode=='vi_auction':
            self.mode='closing_auction';self.until=max(self.until,23400+self.rng.uniform(0,self.jitter))
            self.event('closing_auction_start',until=self.until)

    def breach(self,price,closing=False):
        rate=self.closing_rate if closing else self.dynamic_rate
        # Compare in integer-friendly percentage space; inclusive trigger boundary.
        dynamic=abs(price-self.book.last_price)*100 >= self.book.last_price*rate*100-1e-8
        static=abs(price-self.static_reference)*100 >= self.static_reference*self.static_rate*100-1e-8
        return ('dynamic_and_static' if dynamic and static else 'dynamic' if dynamic else 'static' if static else '')

    def interrupt(self,price):
        kind=self.breach(price)
        if not kind:return False
        self.mode='vi_auction';self.protected=True
        self.until=self.now+120+self.rng.uniform(0,self.jitter)
        self.event('vi_start',kind=kind,trigger_price=price,until=self.until)
        return True

    def park(self,side,quantity,price,sequence):
        from Daily_bot.backtest.fake_market import Order
        if price is None:self.markets[side].append(Order(sequence,quantity))
        else:
            levels=self.book.bids if side=='buy' else self.book.asks
            levels.setdefault(price,deque()).append(Order(sequence,quantity))

    def indicative(self):
        b=self.book
        candidates=set(b.bids)|set(b.asks)|{b.last_price}
        mb=sum(o.quantity for o in self.markets['buy']);ms=sum(o.quantity for o in self.markets['sell'])
        scored=[]
        for p in candidates:
            demand=mb+sum(sum(o.quantity for o in q) for price,q in b.bids.items() if price>=p)
            supply=ms+sum(sum(o.quantity for o in q) for price,q in b.asks.items() if price<=p)
            scored.append((min(demand,supply),-abs(demand-supply),-abs(p-b.last_price),-p,p))
        best=max(scored)
        return best[-1],best[0]

    def uncross(self):
        from Daily_bot.backtest.fake_market import Execution
        b=self.book;p,volume=self.indicative()
        if not volume:return []
        queues={}
        for side,levels in [('buy',b.bids),('sell',b.asks)]:
            quotes=[(None,self.markets[side])]+[(price,levels[price]) for price in sorted(levels,reverse=side=='buy')
                     if (price>=p if side=='buy' else price<=p)]
            queues[side]=deque((price,q) for price,q in quotes if q)
        left=volume;fills=[]
        while left:
            bp,bq=queues['buy'][0];sp,sq=queues['sell'][0]
            amount=min(left,bq[0].quantity,sq[0].quantity)
            fills.append(Execution(p,amount,sq[0].sequence,'auction'))
            left-=amount;bq[0].quantity-=amount;sq[0].quantity-=amount
            for side,price,q in [('buy',bp,bq),('sell',sp,sq)]:
                if not q[0].quantity:q.popleft()
                if not q:
                    queues[side].popleft()
                    if price is not None:del (b.bids if side=='buy' else b.asks)[price]
        # Synthetic market-order residuals expire (explicit IOC approximation).
        for q in self.markets.values():q.clear()
        b.last_price=p;self.static_reference=p
        self.auction_price=p;self.auction_volume=volume
        self.event('auction_uncross',price=p,quantity=volume,auction=self.mode)
        return fills

    def finish_tick(self):
        if self.mode=='closing_auction' and not self.protected:
            p,volume=self.indicative();kind=self.breach(p,closing=True) if volume else ''
            if kind:
                self.protected=True;self.until=max(self.until,self.now+120+self.rng.uniform(0,self.jitter))
                self.event('vi_start',kind=kind,trigger_price=p,until=self.until,closing=True)
        if self.mode in {'vi_auction','closing_auction'} and self.now>=self.until:
            closing=self.mode=='closing_auction'
            fills=self.uncross()
            if not fills:
                if closing:
                    # No crossing at session end: last valid execution is the close.
                    for q in self.markets.values():q.clear()
                    self.event('auction_no_cross')
                else:
                    self.until=self.now+120+self.rng.uniform(0,self.jitter)
                    self.event('auction_no_cross_extension',until=self.until)
                    return []
            self.mode='closed' if closing else 'continuous';self.protected=False
            return fills
        return []

    def raw(self):
        return dict(market_state='auction_uncross' if self.auction_volume else self.mode,
            auction_price=self.auction_price,auction_volume=self.auction_volume,
            auction_until_seconds=self.until if self.mode.endswith('auction') else None,
            market_events=self.tick_events)
