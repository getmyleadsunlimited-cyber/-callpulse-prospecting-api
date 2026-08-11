from datetime import datetime, timedelta, timezone

def test_campaign_creation_has_seven_day_defaults(client):
    r=client.post('/campaigns',json={"name":"Houston Roofing","industry":"Roofing","geography":"Houston, TX","start_date":"2026-08-11"})
    assert r.status_code==200; assert r.json()["end_date"]=="2026-08-17"; assert r.json()["status"]=="draft"
def test_qualification_rejects_low_score(campaign,client,prospect_payload):
    prospect_payload["score"]=64
    r=client.post(f"/campaigns/{campaign}/prospects",json=prospect_payload)
    assert r.status_code==409; assert "score_below_minimum" in r.json()["detail"]["reasons"]
def test_duplicate_prevention(campaign,client,prospect_payload):
    assert client.post(f"/campaigns/{campaign}/prospects",json=prospect_payload).status_code==200
    assert client.post(f"/campaigns/{campaign}/prospects",json=prospect_payload).status_code==409
def queued(campaign,client,p): return client.post(f"/campaigns/{campaign}/prospects",json=p).json()
def test_suppression_and_stop_on_opt_out(campaign,client,prospect_payload):
    p=queued(campaign,client,prospect_payload); r=client.post('/events/opt-out',json={"prospect_id":p["id"]})
    assert r.json()["opted_out"] and r.json()["next_send_at"] is None
    assert client.get(f"/campaigns/{campaign}/followups").json()==[]
def test_stop_on_reply(campaign,client,prospect_payload):
    p=queued(campaign,client,prospect_payload); r=client.post('/events/replies',json={"prospect_id":p["id"],"reply_text":"Interested"})
    assert r.json()["reply_detected"] and r.json()["next_send_at"] is None
    event={"prospect_id":p["id"],"sequence_step":0,"idempotency_key":"x","provider_message_id":"graph-x"}
    assert client.post(f"/campaigns/{campaign}/deliveries",json=event).status_code==409
def test_hard_bounce_suppression(campaign,client,prospect_payload):
    p=queued(campaign,client,prospect_payload); r=client.post('/events/bounce',json={"prospect_id":p["id"],"hard":True})
    assert r.json()["bounced"] and r.json()["suppression_status"]=="hard_bounce"
def test_followup_schedule_and_maximum(campaign,client,prospect_payload):
    p=queued(campaign,client,prospect_payload)
    previous=None
    for step in range(3):
        r=client.post(f"/campaigns/{campaign}/deliveries",json={"prospect_id":p["id"],"sequence_step":step,"idempotency_key":f"key-{step}","provider_message_id":f"graph-{step}"})
        assert r.status_code==200
        if step<2:
            due=datetime.fromisoformat(r.json()["next_send_at"]); sent=datetime.fromisoformat(r.json()["delivered_at"])
            assert timedelta(days=2,hours=23,minutes=59) < due-sent < timedelta(days=3,minutes=1)
    assert r.json()["next_send_at"] is None
def test_idempotent_delivery(campaign,client,prospect_payload):
    p=queued(campaign,client,prospect_payload); event={"prospect_id":p["id"],"sequence_step":0,"idempotency_key":"stable","provider_message_id":"graph-1"}
    first=client.post(f"/campaigns/{campaign}/deliveries",json=event); second=client.post(f"/campaigns/{campaign}/deliveries",json=event)
    assert first.status_code==second.status_code==200; assert second.json()["idempotent_replay"] is True
    assert client.get(f"/campaigns/{campaign}/stats").json()["messages_sent"]==1
