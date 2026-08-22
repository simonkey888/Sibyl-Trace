import fs from 'node:fs';
import { computeBuyPrices } from '/opt/agents-starter/src/strategies/cross-market-mm/index.ts';

type Fixture = {
  poly_bid: number;
  poly_ask: number;
  margin_bps: number;
  yes_book: { bid: number | null; ask: number | null } | null;
};

const path = process.argv[2];
if (!path) throw new Error('FIXTURE_PATH_REQUIRED');
const fixtures = JSON.parse(fs.readFileSync(path, 'utf8')) as Fixture[];
const results = fixtures.map((f) => computeBuyPrices(
  f.poly_bid,
  f.poly_ask,
  f.margin_bps,
  f.yes_book ?? undefined,
));
process.stdout.write(JSON.stringify(results));
