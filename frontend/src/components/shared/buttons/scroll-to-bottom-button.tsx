import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import ArrowSendIcon from "#/icons/arrow-send.svg?react";

interface ScrollToBottomButtonProps {
  onClick: () => void;
}

export function ScrollToBottomButton({ onClick }: ScrollToBottomButtonProps) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid="scroll-to-bottom"
      className="button-base p-1 hover:bg-neutral-500 rotate-180 cursor-pointer"
      aria-label={t(I18nKey.BUTTON$SCROLL_TO_BOTTOM)}
    >
      <ArrowSendIcon width={15} height={15} />
    </button>
  );
}
