import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";
import CloseIcon from "#/icons/close.svg?react";

interface RemoveButtonProps {
  onClick: () => void;
  className?: React.HTMLAttributes<HTMLDivElement>["className"];
}

export function RemoveButton({ onClick, className }: RemoveButtonProps) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={t(I18nKey.BUTTON$REMOVE)}
      className={cn(
        "bg-neutral-400 rounded-full w-5 h-5 flex items-center justify-center cursor-pointer",
        className,
      )}
    >
      <CloseIcon width={18} height={18} />
    </button>
  );
}
