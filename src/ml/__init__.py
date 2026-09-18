"""Machine learning for deadline markets, news analysis, and the paper test.

The pieces, in the order data flows through them:

    history.py   resolved markets + their price paths (Manifold now; Polymarket
                 whenever its hosts answer from this connection)
    features.py  one feature function shared by training and live prediction, so
                 the model never sees a feature at training time that it cannot
                 see live
    model.py     walk-forward training, judged against the market price itself
    deadlines.py markets whose deadline is close, with model + EV attached
    newsml.py    news relevance, tone, story clusters, rising terms
    llm.py       optional Claude reading of the headlines for one market
    paper.py     virtual positions, settlement, win rate, calibration
    scheduler.py optional background scan-and-settle loop

The rule every module keeps: the market price is the baseline to beat. A model
that is not measurably better than the price it trades against has no edge, no
matter how its EV column looks, and the UI says so next to every number.
"""
