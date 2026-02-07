import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import PauseIcon from "#/icons/pause.svg?react";

export interface ChatStopButtonProps {
  handleStop: () => void;
}

export function ChatStopButton({ handleStop }: ChatStopButtonProps) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={handleStop}
      data-testid="stop-button"
      className="cursor-pointer"
      aria-label={t(I18nKey.BUTTON$STOP)}
    >
      <PauseIcon className="block max-w-none w-4 h-4" />
    </button>
  );
}
