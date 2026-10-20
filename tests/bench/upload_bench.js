import http from 'k6/http';
import { check } from 'k6';

// WHY open 'b': loads CSV binary buffer once into memory before scenario execution
const filePath = __ENV.CSV_PATH || '/work/tests/bench/data/benchmark_2mb.csv';
const csvBin = open(filePath, 'b');

export const options = {
  discardResponseBodies: false,
  scenarios: {
    upload_stress: {
      executor: 'constant-vus',
      vus: parseInt(__ENV.VUS || '1'),
      duration: __ENV.DURATION || '60s',
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
