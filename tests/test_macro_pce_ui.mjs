import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const source = fs.readFileSync(new URL('../scripts/build_site.py', import.meta.url), 'utf8');

test('macro page preserves China PMI plus CPI and adds US PMI and PCE policy charts', () => {
  assert.equal((source.match(/id="pmiChart"/g) || []).length, 1);
  assert.equal((source.match(/id="usPmiChart"/g) || []).length, 1);
  assert.equal((source.match(/id="pcePolicyChart"/g) || []).length, 1);
  assert.match(source, /中国PMI与CPI/);
  assert.match(source, /中国CPI同比/);
  assert.match(source, /US_PMI_MANUFACTURING/);
  assert.match(source, /US_PMI_SERVICES/);
  assert.match(source, /PCE_YOY/);
  assert.match(source, /FEDFUNDS/);
  assert.match(source, /BEA NIPA 2\.8\.4/);
  assert.match(source, /美联储 H\.15/);
});
