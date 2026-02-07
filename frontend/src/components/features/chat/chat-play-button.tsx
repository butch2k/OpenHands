import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import PlayIcon from "#/icons/play-solid.svg?react";
import { cn } from "#/utils/utils";

export interface ChatResumeAgentButtonProps {
  onAgentResumed: () => void;
  disabled?: boolean;
}

export function ChatResumeAgentButton({
  onAgentResumed,
  disabled = false,
}: ChatResumeAgentButtonProps) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onAgentResumed}
      data-testid="play-button"
      disabled={disabled}
      className={cn("cursor-pointer", disabled && "cursor-not-allowed")}
      aria-label={t(I18nKey.ACTION_BUTTON$RESUME)}
    >
      <PlayIcon className="block max-w-none w-4 h-4" />
    </button>
  );
}
