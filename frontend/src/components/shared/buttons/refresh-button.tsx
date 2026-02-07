import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import Refresh from "#/icons/refresh.svg?react";

interface RefreshButtonProps {
  onClick: (event: React.MouseEvent<HTMLButtonElement>) => void;
}

export function RefreshButton({ onClick }: RefreshButtonProps) {
  const { t } = useTranslation();
  return (
    <button type="button" onClick={onClick} aria-label={t(I18nKey.BUTTON$REFRESH)}>
      <Refresh width={14} height={14} />
    </button>
  );
}
