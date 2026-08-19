import { CircleStop, Mic, X } from "lucide-react";
import { Button, Dialog, Heading, Modal, ModalOverlay } from "react-aria-components";

import type { DictationState } from "../engine/types";
import styles from "./CommandDrawer.module.css";

interface CommandDrawerProps {
  isOpen: boolean;
  dictationState: DictationState;
  shortcut: string;
  isCaptureReady: boolean;
  isPending: boolean;
  onOpenChange: (isOpen: boolean) => void;
  onStart: () => Promise<unknown>;
  onStop: () => Promise<unknown>;
  onCancel: () => Promise<unknown>;
}

export function CommandDrawer({
  isOpen,
  dictationState,
  shortcut,
  isCaptureReady,
  isPending,
  onOpenChange,
  onStart,
  onStop,
  onCancel,
}: CommandDrawerProps) {
  const isRecording = dictationState === "recording";
  const isStartable = ["idle", "completed", "cancelled", "failed"].includes(dictationState);
  const isActive = ["recording", "processing", "cleaning", "inserting"].includes(
    dictationState,
  );

  return (
    <ModalOverlay className={styles.overlay} isOpen={isOpen} onOpenChange={onOpenChange} isDismissable>
      <Modal className={styles.modal}>
        <Dialog className={styles.dialog} aria-label="OpenWhisper commands">
          {({ close }) => (
            <>
              <div className={styles.headingRow}>
                <Heading slot="title">Commands</Heading>
                <Button className={styles.closeButton} onPress={close} aria-label="Close commands">
                  <X aria-hidden="true" size={18} />
                </Button>
              </div>
              <p>Run capture actions here. Your global shortcut is shown exactly as configured.</p>
              <div className={styles.commandList}>
                <Button
                  className={styles.command}
                  isDisabled={isPending || !isCaptureReady || !isStartable}
                  onPress={() => {
                    close();
                    void onStart();
                  }}
                >
                  <Mic aria-hidden="true" size={19} />
                  <span>Start dictation</span>
                  <kbd>{normalizeShortcut(shortcut)}</kbd>
                </Button>
                <Button
                  className={styles.command}
                  isDisabled={isPending || !isRecording}
                  onPress={() => {
                    close();
                    void onStop();
                  }}
                >
                  <CircleStop aria-hidden="true" size={19} />
                  <span>Stop and transcribe</span>
                  <span className={styles.actionType}>Action</span>
                </Button>
                <Button
                  className={styles.command}
                  isDisabled={isPending || !isActive}
                  onPress={() => {
                    close();
                    void onCancel();
                  }}
                >
                  <X aria-hidden="true" size={19} />
                  <span>Cancel this dictation</span>
                  <span className={styles.actionType}>Action</span>
                </Button>
              </div>
            </>
          )}
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}

function normalizeShortcut(shortcut: string) {
  return shortcut
    .split("<")
    .join("")
    .split(">")
    .join("")
    .split("+")
    .join(" + ");
}
