import os

import pandas as pd
from automations.profuomo import ProfuomoScraper

URLS = [
    "https://b2b.profuomo.com/categories/Micro_Fashion_04?categoryCodeForLevel1=Shirts",
    "https://b2b.profuomo.com/categories/Micro_Fashion_04?categoryCodeForLevel1=Overshirts",
    "https://b2b.profuomo.com/categories/Micro_Fashion_04?categoryCodeForLevel1=Wedding%2FFestive",
    "https://b2b.profuomo.com/categories/Micro_Fashion_04?categoryCodeForLevel1=Knitwear",
    "https://b2b.profuomo.com/categories/Micro_Fashion_04?categoryCodeForLevel1=Outerwear",
    "https://b2b.profuomo.com/categories/Micro_Fashion_04?categoryCodeForLevel1=Jackets",
    "https://b2b.profuomo.com/categories/Micro_Fashion_04?categoryCodeForLevel1=Polos",
    "https://b2b.profuomo.com/categories/Micro_Fashion_04?categoryCodeForLevel1=Trousers",
    "https://b2b.profuomo.com/categories/Micro_Fashion_04?categoryCodeForLevel1=Shoes",
    "https://b2b.profuomo.com/categories/Micro_Fashion_04?categoryCodeForLevel1=T-shirts",
    "https://b2b.profuomo.com/categories/Micro_Fashion_04?categoryCodeForLevel1=Ties",
    "https://b2b.profuomo.com/categories/Micro_Fashion_04?categoryCodeForLevel1=Belts",
    "https://b2b.profuomo.com/categories/Micro_Fashion_04?categoryCodeForLevel1=Accessories",
    "https://b2b.profuomo.com/categories/Micro_Fashion_04?categoryCodeForLevel1=Winter%20accessories",
]


def scrape_all():
    import multiprocessing

    with multiprocessing.Pool(processes=4) as pool:
        pool.map(ProfuomoScraper.scrape_profuomo, URLS)


def update_names():
    csvs = (csv for csv in os.listdir("products") if csv.endswith(".csv"))
    for csv in csvs:
        df = pd.read_csv(os.path.join("products", csv))
        df = ProfuomoScraper.update_names(df)
        df.to_csv(os.path.join("products", csv), index=False)


def insert_categories():
    csvs = (csv for csv in os.listdir("products") if csv.endswith(".csv"))
    all = None
    dataframes = []
    for csv in csvs:
        df = pd.read_csv(os.path.join("products", csv))
        category = csv.split(".")[0].lower()
        if category != "all":
            dataframes.append(df)
            if "category" in df.keys():
                continue
            df["category"] = category
            df.to_csv(os.path.join("products", csv), index=False)
        else:
            all = df
    if all is not None:
        sku_category_df = pd.DataFrame(columns=["sku", "category"])

        for df in dataframes:
            sku_category_df = pd.concat(
                [sku_category_df, df[["sku", "category"]]], ignore_index=True
            )

        all = pd.merge(all, sku_category_df, on="sku", how="left")

        all.to_csv(os.path.join("products", "all.csv"), index=False)


if __name__ == "__main__":
    insert_categories()
    pass
