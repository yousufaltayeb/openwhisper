import { useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CircleAlert,
  Keyboard,
  LockKeyhole,
  Mic,
  Radio,
} from "lucide-react";
import {
  Button,
  Checkbox,
  Dialog,
  Heading,
  Modal,
  ModalOverlay,
} from "react-aria-components";

import type { ProviderDto, SettingsDto } from "../engine/types";
import styles from "./Onboarding.module.css";

interface OnboardingProps {
  isOpen: boolean;
  settings: SettingsDto;
  providers: ProviderDto[];
  shortcutStatus: string | null;
  request: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>;
  onComplete: () => void;
}

type CheckState = "idle" | "checking" | "ready" | "attention";

const steps = ["Privacy", "Microphone", "Shortcut", "Local provider", "Ready"] as const;

export function Onboarding({
  isOpen,
  settings,
  providers,
  shortcutStatus,
  request,
  onComplete,
}: OnboardingProps) {
  const [step, setStep] = useState(0);
  const [privacyAccepted, setPrivacyAccepted] = useState(false);
  const [microphoneState, setMicrophoneState] = useState<CheckState>("idle");
  const [microphoneMessage, setMicrophoneMessage] = useState("");
  const [shortcutState, setShortcutState] = useState<CheckState>("idle");
  const [shortcutMessage, setShortcutMessage] = useState("");
  const [finishing, setFinishing] = useState(false);
  const [finishError, setFinishError] = useState("");
  const provider = providers.find((item) => item.id === settings.transcriptionProvider);

  async function testMicrophone() {
    setMicrophoneState("checking");
    try {
      const result = await request<{ ready: boolean; message: string }>("audio.testDevice", {
        deviceId: settings.audioDeviceId,
      });
      setMicrophoneState(result.ready ? "ready" : "attention");
      setMicrophoneMessage(result.message);
    } catch {
      setMicrophoneState("attention");
      setMicrophoneMessage("The microphone test could not complete. Check desktop permission and try again.");
    }
  }

  async function checkShortcut() {
    setShortcutState("checking");
    try {
      const result = await request<Record<string, string>>("diagnostics.run");
      const message = shortcutStatus ?? result["Global shortcut"];
      setShortcutState(message?.startsWith("ready") ? "ready" : "attention");
      setShortcutMessage(message ?? "Shortcut status is not available yet.");
    } catch {
      setShortcutState("attention");
      setShortcutMessage("Shortcut permission could not be confirmed. Capture remains available from this window.");
    }
  }

  async function finish() {
    setFinishing(true);
    setFinishError("");
    try {
      await request("settings.update", { changes: { onboardingCompleted: true } });
      onComplete();
    } catch {
      setFinishError("OpenWhisper could not save onboarding. Your privacy choices were not changed.");
    } finally {
      setFinishing(false);
    }
  }

  const canContinue =
    (step === 0 && privacyAccepted) ||
    (step === 1 && microphoneState !== "idle" && microphoneState !== "checking") ||
    (step === 2 && shortcutState !== "idle" && shortcutState !== "checking") ||
    step === 3;

  return (
    <ModalOverlay className={styles.overlay} isOpen={isOpen} isDismissable={false}>
      <Modal className={styles.modal}>
        <Dialog className={styles.dialog} aria-label="Set up OpenWhisper">
          <div className={styles.progress} aria-label={`Onboarding step ${step + 1} of ${steps.length}`}>
            <span className={styles.wordmark}>OpenWhisper</span>
            <ol>
              {steps.map((label, index) => (
                <li key={label} data-current={index === step} data-complete={index < step}>
                  <span>{index < step ? <Check aria-hidden="true" size={13} /> : index + 1}</span>
                  <span className={styles.stepLabel}>{label}</span>
                </li>
              ))}
            </ol>
          </div>

          <div className={styles.content}>
            {step === 0 ? (
              <Step icon={LockKeyhole} title="Your voice stays behind a narrow boundary.">
                <p>
                  Raw audio never crosses the frontend IPC boundary. The Python engine handles capture,
                  transcription, insertion, and credentials; this interface keeps transcript state in memory only.
                </p>
                <p>
                  Completed transcripts follow your local history retention setting. Audio retention and context
                  sharing are off unless you explicitly enable them later.
                </p>
                <Checkbox
                  className={styles.checkbox}
                  isSelected={privacyAccepted}
                  onChange={setPrivacyAccepted}
                >
                  <span className={styles.checkboxBox} aria-hidden="true">
                    <Check size={14} />
                  </span>
                  I understand what crosses the interface boundary.
                </Checkbox>
              </Step>
            ) : null}

            {step === 1 ? (
              <Step icon={Mic} title="Confirm a clean capture path.">
                <p>The test opens the selected microphone briefly and discards the temporary capture.</p>
                <CheckPanel state={microphoneState} message={microphoneMessage}>
                  <Button className={styles.testButton} onPress={() => void testMicrophone()} isDisabled={microphoneState === "checking"}>
                    <Mic aria-hidden="true" size={17} />
                    {microphoneState === "checking" ? "Testing microphone" : "Test microphone"}
                  </Button>
                </CheckPanel>
              </Step>
            ) : null}

            {step === 2 ? (
              <Step icon={Keyboard} title="Bind capture where you write.">
                <p>
                  Your configured shortcut is <kbd>{formatShortcut(settings.shortcut)}</kbd>. On Wayland, the
                  desktop portal owns permission and may assign a different visible binding.
                </p>
                <CheckPanel state={shortcutState} message={shortcutMessage}>
                  <Button className={styles.testButton} onPress={() => void checkShortcut()} isDisabled={shortcutState === "checking"}>
                    <Keyboard aria-hidden="true" size={17} />
                    {shortcutState === "checking" ? "Checking shortcut" : "Check shortcut status"}
                  </Button>
                </CheckPanel>
              </Step>
            ) : null}

            {step === 3 ? (
              <Step icon={Radio} title="Start private by default.">
                <p>The selected transcription provider is checked before Capture becomes available.</p>
                <div className={styles.providerStatus} data-ready={provider?.available === true}>
                  {provider?.available ? <Check aria-hidden="true" size={20} /> : <CircleAlert aria-hidden="true" size={20} />}
                  <div>
                    <strong dir="auto">{provider?.name ?? settings.transcriptionProvider}</strong>
                    <span>{provider?.available ? provider.description : provider?.unavailableReason ?? "This provider is unavailable."}</span>
                  </div>
                </div>
              </Step>
            ) : null}

            {step === 4 ? (
              <Step icon={Check} title="Capture is calibrated.">
                <p>
                  Use the recording control or <kbd>{formatShortcut(settings.shortcut)}</kbd>. If direct insertion
                  is blocked, OpenWhisper preserves the transcript and uses its clipboard fallback.
                </p>
                {finishError ? <p className={styles.finishError} role="alert">{finishError}</p> : null}
              </Step>
            ) : null}
          </div>

          <div className={styles.actions}>
            <Button className={styles.backButton} onPress={() => setStep((value) => Math.max(0, value - 1))} isDisabled={step === 0 || finishing}>
              <ArrowLeft aria-hidden="true" size={17} />
              Back
            </Button>
            {step < steps.length - 1 ? (
              <Button className={styles.nextButton} onPress={() => setStep((value) => Math.min(steps.length - 1, value + 1))} isDisabled={!canContinue}>
                Continue
                <ArrowRight aria-hidden="true" size={17} />
              </Button>
            ) : (
              <Button className={styles.nextButton} onPress={() => void finish()} isDisabled={finishing || provider?.available !== true}>
                {finishing ? "Saving setup" : "Open Capture"}
                <ArrowRight aria-hidden="true" size={17} />
              </Button>
            )}
          </div>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}

function Step({
  icon: Icon,
  title,
  children,
}: {
  icon: typeof LockKeyhole;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className={styles.step}>
      <Icon className={styles.stepIcon} aria-hidden="true" size={28} strokeWidth={1.5} />
      <Heading slot="title">{title}</Heading>
      {children}
    </section>
  );
}

function CheckPanel({
  state,
  message,
  children,
}: {
  state: CheckState;
  message: string;
  children: React.ReactNode;
}) {
  return (
    <div className={styles.checkPanel} data-state={state}>
      {children}
      {message ? (
        <p role={state === "attention" ? "alert" : "status"}>
          {state === "ready" ? <Check aria-hidden="true" size={17} /> : <CircleAlert aria-hidden="true" size={17} />}
          <span>{message}</span>
        </p>
      ) : null}
    </div>
  );
}

function formatShortcut(shortcut: string) {
  return shortcut
    .split("<")
    .join("")
    .split(">")
    .join("")
    .split("+")
    .join(" + ");
}
