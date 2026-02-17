import os

import pandas as pd
from automations.profuomo import ProfuomoScraper

URLS = [
    "https://b2b.profuomo.com/webstore/v2/products/Micro_Fashion_04/Shirts?5",
    "https://b2b.profuomo.com/webstore/v2/products/Micro_Fashion_04/Overshirts?7",
    "https://b2b.profuomo.com/webstore/v2/products/Micro_Fashion_04/Wedding?9",
    "https://b2b.profuomo.com/webstore/v2/products/Micro_Fashion_04/Knitwear?11",
    "https://b2b.profuomo.com/webstore/v2/products/Micro_Fashion_04/Outerwear?13",
    "https://b2b.profuomo.com/webstore/v2/products/Micro_Fashion_04/Jackets?15",
    "https://b2b.profuomo.com/webstore/v2/products/Micro_Fashion_04/Polos?17",
    "https://b2b.profuomo.com/webstore/v2/products/Micro_Fashion_04/Trousers?19",
    "https://b2b.profuomo.com/webstore/v2/products/Micro_Fashion_04/Shoes?21",
    "https://b2b.profuomo.com/webstore/v2/products/Micro_Fashion_04/T-shirts?23",
    "https://b2b.profuomo.com/webstore/v2/products/Micro_Fashion_04/Ties?25",
    "https://b2b.profuomo.com/webstore/v2/products/Micro_Fashion_04/Belts?27",
    "https://b2b.profuomo.com/webstore/v2/products/Micro_Fashion_04/Accessories?29",
    "https://b2b.profuomo.com/webstore/v2/products/Micro_Fashion_04/Winter%20accessories?31",
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
