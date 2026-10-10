"""Prueba de una sesión: un reloj controller y dos o más Pico responder."""
import argparse
import json
import queue
import time
import uuid
import paho.mqtt.client as mqtt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--broker', default='192.168.2.168')
    parser.add_argument('--watch', default='watch_01')
    parser.add_argument('--anchors', nargs='+', required=True)
    parser.add_argument('--unicast', action='store_true')
    args = parser.parse_args()
    anchors = list(dict.fromkeys(args.anchors))
    if args.unicast and len(anchors) != 1:
        parser.error('unicast requiere una sola placa')
    events = queue.Queue()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='hera_multicast_test_' + uuid.uuid4().hex[:8])
    client.on_message = lambda c, u, msg: events.put((msg.topic, json.loads(msg.payload), msg.retain))
    def connected(c, u, flags, reason, properties):
        for topic in ('uwb/session/' + args.watch, 'uwb/pico/+/events'):
            c.subscribe(topic, qos=1)
    client.on_connect = connected
    def send(topic, body):
        client.publish(topic, json.dumps(body), qos=1)
        print('TX', topic, body, flush=True)
    client.connect(args.broker, 1883, 15)
    client.loop_start()
    sid = 'multicast_' + uuid.uuid4().hex
    uwb_id = (uuid.uuid4().int & 0x7fffffff) or 1
    pending = set(anchors)
    closed = set()
    watch_closed = False
    try:
        # Esperar confirmación SUBACK antes de publicar el primer START.
        time.sleep(1)
        start = time.monotonic()
        send('uwb/commands/' + args.watch, {'action': 'start', 'session_id': sid,
             'anchor': anchors[0], 'anchors': anchors, 'multicast': not args.unicast,
             'uwb_session_id': uwb_id, 'timelimit_ms': 5000})
        started = False
        while time.monotonic() - start < 5:
            try:
                topic, body, retained = events.get(timeout=.1)
            except queue.Empty:
                continue
            if retained or body.get('session_id') != sid:
                continue
            print('RX', topic, body, flush=True)
            if body.get('event') in ('result', 'closed', 'ready', 'busy'):
                anchor = topic.split('/')[2]
                send('uwb/pico/' + anchor + '/commands', {'action': 'ack', 'session_id': sid, 'event': body['event']})
                if body['event'] == 'result':
                    pending.discard(anchor)
                if body['event'] == 'closed':
                    closed.add(anchor)
            if body.get('state') == 'stopped':
                watch_closed = True
            if body.get('state') == 'prepared' and not started:
                started = True
                for anchor in anchors:
                    send('uwb/pico/' + anchor + '/commands', {'action': 'start', 'session_id': sid,
                         'watch_id': args.watch, 'watch_mac': body['uwb_mac'], 'multicast': not args.unicast,
                         'uwb_session_id': uwb_id, 'remaining_ms': max(1, int((5 - (time.monotonic()-start))*1000))})
            if not pending:
                break
        print('Placas sin resultado:', sorted(pending))
    finally:
        # Cierre explícito aunque la prueba se interrumpa.
        send('uwb/commands/' + args.watch, {'action': 'stop', 'session_id': sid})
        for anchor in anchors:
            send('uwb/pico/' + anchor + '/commands', {'action': 'stop', 'session_id': sid, 'watch_id': args.watch})
        time.sleep(1)
        client.disconnect()
        client.loop_stop()


if __name__ == '__main__':
    main()
