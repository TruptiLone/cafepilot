import { CoffeeIcon } from "./Icons";

type BrandProps = {
  compact?: boolean;
};

function Brand({ compact = false }: BrandProps) {
  return (
    <div className="brand" aria-label="CaféPilot Business Management">
      <span className="brand__mark" aria-hidden="true">
        <CoffeeIcon />
      </span>
      <span className="brand__name">CaféPilot</span>
      {!compact && <span className="brand__product">Business Management</span>}
    </div>
  );
}

export default Brand;
