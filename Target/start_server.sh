#!/bin/bash
gunicorn target_HTTPs:app --bind 0.0.0.0:8443 --certfile target1.crt --keyfile target1.key --ca-certs ca.crt --cert-reqs 2
