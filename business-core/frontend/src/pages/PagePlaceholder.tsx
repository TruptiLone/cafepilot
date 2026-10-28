type PagePlaceholderProps = {
  title: string;
  description: string;
};

function PagePlaceholder({ title, description }: PagePlaceholderProps) {
  return (
    <section className="page" aria-labelledby="page-title">
      <div className="page__heading">
        <div>
          <p className="page__eyebrow">Business Management</p>
          <h1 id="page-title">{title}</h1>
          <p className="page__description">{description}</p>
        </div>
        <span className="status-chip">Workspace ready</span>
      </div>

      <div className="placeholder-card">
        <div className="placeholder-card__icon" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <h2>{title} workspace</h2>
        <p>
          This area is ready for the upcoming {title.toLowerCase()} screens and
          workflows.
        </p>
      </div>
    </section>
  );
}

export default PagePlaceholder;
