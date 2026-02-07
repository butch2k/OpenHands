import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import CloseIcon from "#/icons/u-close.svg?react";
import { cn, isMobileDevice } from "#/utils/utils";

interface RemoveFileButtonProps {
  onClick: () => void;
}

export function RemoveFileButton({ onClick }: RemoveFileButtonProps) {
  const { t } = useTranslation();
  const isMobile = isMobileDevice();

  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={t(I18nKey.BUTTON$REMOVE_FILE)}
      className={cn(
        "flex w-4 h-4 rounded-full items-center justify-center bg-[#25272D] hover:bg-[#A1A1A1] cursor-pointer absolute top-1 right-1 opacity-0 group-hover:opacity-100 transition-opacity duration-200",
        isMobile && "opacity-100",
      )}
    >
      <CloseIcon width={10} height={10} color="#ffffff" />
    </button>
  );
}
