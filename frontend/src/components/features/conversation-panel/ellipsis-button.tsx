import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import ThreeDotsVerticalIcon from "#/icons/three-dots-vertical.svg?react";

interface EllipsisButtonProps {
  onClick: (event: React.MouseEvent<HTMLButtonElement>) => void;
  fill?: string;
}

export function EllipsisButton({
  onClick,
  fill = "#a3a3a3",
}: EllipsisButtonProps) {
  const { t } = useTranslation();
  return (
    <button
      data-testid="ellipsis-button"
      type="button"
      onClick={onClick}
      className="cursor-pointer"
      aria-label={t(I18nKey.COMMON$MORE_OPTIONS)}
    >
      <ThreeDotsVerticalIcon width={24} height={24} color={fill} />
    </button>
  );
}
