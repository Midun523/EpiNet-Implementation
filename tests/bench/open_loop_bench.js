import http from 'k6/http';
import { check } from 'k6';

const filePath = __ENV.CSV_PATH || '/work/tests/bench/data/benchmark_2mb.csv';
const csvBin = open(filePath, 'b');

export const options = {
  discardResponseBodies: false,
  scenarios: {
    open_loop_arrivals: {
      executor: 'constant-arrival-rate',
      rate: parseInt(__ENV.RATE || '20'), // arrivals per timeUnit
      timeUnit: '1m',                      // 20/min or 60/min
      duration: __ENV.DURATION || '30s',
      preAllocatedVUs: 10,
      maxVUs: 30,
      gracefulStop: '15s',
    },
  },
};

export default function () {
  const data = {
    file: http.file(csvBin, 'bench_upload.csv', 'text/csv'),
  };

  const url = `${__ENV.TARGET_URL || 'http://host.docker.internal:30081'}/api/uploads`;
  const res = http.post(url, data);

  check(res, {
    'upload accepted (HTTP 202)': (r) => r.status === 202,
  });
}
