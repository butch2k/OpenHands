import { ArrowUp } from "lucide-react";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";

export interface ChatSendButtonProps {
  buttonClassName: string;
  handleSubmit: () => void;
  disabled: boolean;
}

export function ChatSendButton({
  buttonClassName,
  handleSubmit,
  disabled,
}: ChatSendButtonProps) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      className={cn(
        "flex items-center justify-center rounded-full border border-white size-[35px]",
        disabled
          ? "cursor-not-allowed border-neutral-600"
          : "cursor-pointer hover:bg-[#959CB2]",
        buttonClassName,
      )}
      data-name="arrow-up-circle-fill"
      data-testid="submit-button"
      onClick={handleSubmit}
      disabled={disabled}
      aria-label={t(I18nKey.BUTTON$SEND)}
    >
      <ArrowUp color={disabled ? "#959CB2" : "white"} />
    </button>
  );
}
