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
  let wordErrors = 0, wordTotal = 0, charErrors = 0, charTotal = 0;
  let termsFound = 0, termsTotal = 0, entitiesFound = 0, entitiesTotal = 0;
  for (const sample of items) {
    const referenceWords = words(sample.reference), predictionWords = words(sample.prediction);
    const referenceChars = chars(sample.reference), predictionChars = chars(sample.prediction);
    wordErrors += distance(referenceWords, predictionWords); wordTotal += referenceWords.length;
    charErrors += distance(referenceChars, predictionChars); charTotal += referenceChars.length;
    const prediction = normalize(sample.prediction).toLocaleLowerCase("und");
    for (const term of sample.technical_terms) { termsTotal += 1; if (prediction.includes(normalize(term).toLocaleLowerCase("und"))) termsFound += 1; }
    for (const entity of sample.entities) { entitiesTotal += 1; if (prediction.includes(normalize(entity).toLocaleLowerCase("und"))) entitiesFound += 1; }
  }
  return {
    samples: items.length,
    wer: wordTotal ? wordErrors / wordTotal : 0,
    cer: charTotal ? charErrors / charTotal : 0,
    technical_term_recall: termsTotal ? termsFound / termsTotal : 1,
    named_entity_recall: entitiesTotal ? entitiesFound / entitiesTotal : 1,
  };
}

const all = aggregate(samples);
const by_subset = Object.fromEntries(["arabic", "mixed", "english"].map((subset) => [subset, aggregate(samples.filter((sample) => sample.subset === subset))]));
const result = {
  schema: 1,
  normalization: "openwhisper-benchmark-v1",
  ...all,
  mixed_error_rate: by_subset.mixed.wer,
  by_subset,
  input_sha256: new Bun.CryptoHasher("sha256").update(raw).digest("hex"),
};
process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
