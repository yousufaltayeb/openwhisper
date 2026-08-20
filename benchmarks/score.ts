import { readFileSync } from "node:fs";

interface Sample {
  id: string;
  subset: "arabic" | "mixed" | "english";
  reference: string;
  prediction: string;
  technical_terms: string[];
  entities: string[];
}

const input = process.argv[2];
if (!input) throw new Error("usage: bun benchmarks/score.ts <predictions.jsonl>");
const raw = readFileSync(input, "utf8");
const samples = raw.trim().split("\n").filter(Boolean).map((line) => JSON.parse(line) as Sample);
if (samples.length === 0) throw new Error("benchmark input is empty");

const normalize = (text: string) => text.normalize("NFC").trim().replace(/\s+/gu, " ");
const words = (text: string) => normalize(text).split(" ").filter(Boolean);
const chars = (text: string) => [...normalize(text).replace(/\s/gu, "")];
const distance = <T>(left: T[], right: T[]) => {
  let previous = right.map((_, index) => index + 1);
  for (let row = 0; row < left.length; row += 1) {
    const current = [row + 1];
    for (let column = 0; column < right.length; column += 1) {
      current.push(Math.min((current[column] ?? 0) + 1, (previous[column + 1] ?? 0) + 1, (previous[column] ?? 0) + (left[row] === right[column] ? 0 : 1)));
    }
    previous = current;
  }
  return previous[right.length] ?? left.length;
};

function aggregate(items: Sample[]) {
  return summarize(items.map(measure));
}

interface Measurement {
  wordErrors: number; wordTotal: number; charErrors: number; charTotal: number;
  termsFound: number; termsTotal: number; entitiesFound: number; entitiesTotal: number;
}

function measure(sample: Sample): Measurement {
  const referenceWords = words(sample.reference), predictionWords = words(sample.prediction);
  const referenceChars = chars(sample.reference), predictionChars = chars(sample.prediction);
  const prediction = normalize(sample.prediction).toLocaleLowerCase("und");
  let termsFound = 0, entitiesFound = 0;
  for (const term of sample.technical_terms) if (prediction.includes(normalize(term).toLocaleLowerCase("und"))) termsFound += 1;
  for (const entity of sample.entities) if (prediction.includes(normalize(entity).toLocaleLowerCase("und"))) entitiesFound += 1;
  return {
    wordErrors: distance(referenceWords, predictionWords), wordTotal: referenceWords.length,
    charErrors: distance(referenceChars, predictionChars), charTotal: referenceChars.length,
    termsFound, termsTotal: sample.technical_terms.length,
    entitiesFound, entitiesTotal: sample.entities.length,
  };
}

function summarize(measurements: Measurement[]) {
  let wordErrors = 0, wordTotal = 0, charErrors = 0, charTotal = 0;
  let termsFound = 0, termsTotal = 0, entitiesFound = 0, entitiesTotal = 0;
  for (const item of measurements) {
    wordErrors += item.wordErrors; wordTotal += item.wordTotal;
    charErrors += item.charErrors; charTotal += item.charTotal;
    termsFound += item.termsFound; termsTotal += item.termsTotal;
    entitiesFound += item.entitiesFound; entitiesTotal += item.entitiesTotal;
  }
  return {
    samples: measurements.length,
    wer: wordTotal ? wordErrors / wordTotal : 0,
    cer: charTotal ? charErrors / charTotal : 0,
    technical_term_recall: termsTotal ? termsFound / termsTotal : 1,
    named_entity_recall: entitiesTotal ? entitiesFound / entitiesTotal : 1,
  };
}

function bootstrapIntervals(items: Sample[], seed: number) {
  const measurements = items.map(measure);
  const random = mulberry32(seed);
  const wer: number[] = [], cer: number[] = [];
  for (let iteration = 0; iteration < 1_000; iteration += 1) {
    const sample = Array.from({ length: measurements.length }, () => measurements[Math.floor(random() * measurements.length)]!);
    const result = summarize(sample);
    wer.push(result.wer); cer.push(result.cer);
  }
  return { wer: interval(wer), cer: interval(cer) };
}

function interval(values: number[]) {
  values.sort((left, right) => left - right);
  return { lower: values[Math.floor(values.length * 0.025)] ?? 0, upper: values[Math.floor(values.length * 0.975)] ?? 0, confidence: 0.95, iterations: 1_000 };
}

function mulberry32(seed: number) {
  return () => {
    seed |= 0; seed = seed + 0x6D2B79F5 | 0;
    let value = Math.imul(seed ^ seed >>> 15, 1 | seed);
    value = value + Math.imul(value ^ value >>> 7, 61 | value) ^ value;
    return ((value ^ value >>> 14) >>> 0) / 4_294_967_296;
  };
}

const all = aggregate(samples);
const by_subset = Object.fromEntries(["arabic", "mixed", "english"].map((subset) => [subset, aggregate(samples.filter((sample) => sample.subset === subset))]));
const mixed = samples.filter((sample) => sample.subset === "mixed");
const result = {
  schema: 1,
  normalization: "openwhisper-benchmark-v1",
  ...all,
  mixed_error_rate: by_subset.mixed.wer,
  confidence_intervals: {
    ...bootstrapIntervals(samples, 20_260_820),
    mixed_error_rate: bootstrapIntervals(mixed, 20_260_821).wer,
  },
  by_subset,
  input_sha256: new Bun.CryptoHasher("sha256").update(raw).digest("hex"),
};
process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
