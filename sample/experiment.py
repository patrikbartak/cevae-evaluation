import numpy as np

from compare import *
from typing import *
from scipy.stats import multivariate_normal, beta
from datetime import datetime
import pyro.distributions as dist

class Experiment:
    """
    Creates an experiment builder which can be used to create some specific experiments.
    """

    def __init__(self, seed: int = None, name: str = None):
        self.name = name
        self.reset(seed)

    def __hash__(self):
        if self.name is not None:
            return self.name
        return super().__hash__()

    def reset(self, seed: int = None):
        """
        Resets the experiment.
        :param seed: seed for random state.
        :return: self
        """
        self.results: List[pd.DataFrame] = []
        self.generators: List[(Generator, int)] = []
        self.models: List[CausalMethod] = []
        self.metrics: Dict[str, Callable[[List[float], List[float]], float]] = {}
        if seed is not None:
            np.random.seed(seed)
        self.seed = seed
        self._set_defaults()
        self.trained: bool = False
        self.count: int = 0
        seed = f"seeded_{seed}" if seed is not None else f"randomized"
        datetime_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        hash = f"{self.__hash__()}"
        self.directory = f'experiments/experiment_{seed}_{datetime_str}_{hash}'
        os.makedirs(self.directory, exist_ok=True)
        return self

    def clear(self):
        """
        Completely clear generated data, but keep the models.
        :return: self
        """
        self.results = []
        self.generators = []
        self._set_defaults()
        self.trained = False
        return self

    def get_result(self):
        return self.results[0]['eATE'].iat[0]

    def add_custom_generator(self, generator: Generator, sample_size: int = 500):
        """
        Adds a custom generator.
        :param generator: Generator to be added
        :param sample_size: Number of samples to be generated by the generator
        :return: self
        """
        self.generators.append((generator, sample_size))
        return self

    def add_custom_model(self, model: CausalMethod):
        """
        Add a causal model into the experiment
        :param model: Model to be added
        :return: self
        """
        self.models.append(model)
        return self

    def add_custom_metric(self, name: str, scoring_function: Callable[[List[float]], float]):
        """
        Add a custom metric to the experiment.
        :param name: Name of the metric
        :param scoring_function: Lambda function that takes in an array of results and outputs a number
        :return: self
        """
        self.metrics[name] = scoring_function
        return self

    # RUN EXPERIMENT

    def run(self, save_data: bool = True, save_graphs: bool = True, show_graphs: bool = False):
        """
        Runs the experiment. First trains all the models and then evaluates them.
        :param save_data: Boolean whether the generated data should be stored
        :param save_graphs: Boolean whether graphs should be stored
        :param show_graphs: Boolean whether graphs should be shown
        :return: self
        """
        model_dictionary = {}
        for model in self.models:
            model_dictionary[str(model)] = model
        metric_dictionary = self.metrics
        results = np.array([0 for _ in metric_dictionary])
        for generator, sample_size in self.generators:
            generator.directory = self.directory + "/" + generator.directory[5:]
            generator.generate_data(sample_size, save_data=save_data, save_graphs=save_graphs, show_graphs=show_graphs)
            result = run(model_dictionary, metric_dictionary,
                         data_file=generator.directory + generator.generated_files['data'][-1],
                         samples=sample_size, save_table=save_data,
                         dir=generator.directory, show_graphs=show_graphs, save_graphs=save_graphs).to_numpy()
            results = results + result

        results = results / len(self.generators)
        final_results = []
        for index, result in enumerate(results):
            result = list(result)
            result.insert(0, list(model_dictionary.keys())[index])
            final_results.append(result)
        columns = list(metric_dictionary.keys())
        columns.insert(0, 'method_name')
        final_result = pd.DataFrame(final_results, columns=columns)
        final_result = final_result.set_index('method_name')
        self.results.append(final_result)
        save_pandas_table(self.directory + '/final_table', final_result)
        self.trained = True
        return self

    # For each model run this specific test_set
    # Compare with given metrics
    def test_specific_set(self, test_set=pd.DataFrame, truth_set=pd.DataFrame):
        """
        Tests all the trained models on specific data set provided by the user. Make sure to first run the 'run' method.
        :param test_set: Dataframe of feature vectors to be tested
        :param truth_set: Dataframe of corresponding expected results
        :return: self
        """
        assert self.trained, "Models are not trained yet. Please make sure you run the full experiment first!"
        self.count += 1
        columns = [name for name in self.metrics]
        columns.insert(0, 'method_name')
        df = pd.DataFrame([], columns=columns)
        for model in self.models:
            predictions = model.estimate_causal_effect(test_set)
            row = [model.__str__()]
            for metric in self.metrics.values():
                score = metric(truth_set.to_numpy(), predictions)
                row.append(score)
            df.loc[len(df.index)] = row
        df = df.set_index('method_name')
        self.results.append(df)
        save_pandas_table(self.directory + f'/table_comparing_specific_value_{self.count}', df)
        return self

    # MODELS

    def add_causal_forest(self, number_of_trees=100, min_leaf_size=10, honest: bool=True):
        return self.add_custom_model(CausalForest(number_of_trees, k=min_leaf_size, honest=honest, id=len(self.models)))

    def add_dragonnet(self, dimensions):
        return self.add_custom_model(DragonNet(dimensions, id=len(self.models)))

    def add_cevae(self,
                  dimensions,
                  outcome_dist="bernoulli",
                  latent_dim=20,
                  hidden_dim=200,
                  num_layers=3,
                  num_samples=100,
                  batch_size=100,
    ):
        return self.add_custom_model(
            CausalEffectVariationalAutoencoder(
                dimensions,
                outcome_dist,
                latent_dim,
                hidden_dim,
                num_layers,
                num_samples,
                batch_size,
                id=len(self.models)
            )
        )

    # METRICS

    def add_all_metrics(self):
        return self.add_ate_error()\
            .add_ate_percent_error()\
            .add_true_ate()\
            .add_estimated_ate()\
            .add_pehe_mse()\
            .add_pehe_mae()

    def add_true_ate(self):
        return self.add_custom_metric('True ATE',
                                      lambda ite_truth, ite_pred: np.mean(ite_truth))

    def add_estimated_ate(self):
        return self.add_custom_metric('Est. ATE',
                                      lambda ite_truth, ite_pred: np.mean(ite_pred))

    def add_mean_squared_error(self):
        return self.add_custom_metric('PEHE (MSE)',
                                      lambda ite_truth, ite_pred: np.sum(
                                          [(ite_truth[i] - ite_pred[i]) ** 2 for i in range(len(ite_truth))]) / np.prod(ite_truth.shape))

    def add_pehe_mse(self):
        return self.add_mean_squared_error()

    def add_absolute_error(self):
        return self.add_custom_metric('PEHE (MAE)',
                                      lambda ite_truth, ite_pred: np.sum(
                                          [abs(ite_truth[i] - ite_pred[i]) for i in range(len(ite_truth))]) / np.prod(ite_truth.shape))

    def add_pehe_mae(self):
        return self.add_absolute_error()

    def add_ate_error(self):
        return self.add_custom_metric('eATE',
                                      lambda ite_truth, ite_pred: np.abs(np.mean(ite_truth) - np.mean(ite_pred)))

    def add_ate_percent_error(self):
        return self.add_custom_metric('eATE (%)',
                                      lambda ite_truth, ite_pred: np.abs((np.mean(ite_truth) - np.mean(ite_pred)) / np.mean(ite_truth)) * 100)

    # DATA GENERATORS

    def _set_defaults(self):
        """
        Sets the default functions used throughout the project.
        """
        self.main_effect = lambda x: 2 * x[0] - 1
        self.treatment_effect = lambda x: (1 + 1 / (1 + np.exp(-20 * (x[0] - 1 / 3)))) * (
                1 + 1 / (1 + np.exp(-20 * (x[1] - 1 / 3))))
        # https://en.wikipedia.org/wiki/Beta_distribution
        self.treatment_propensity = lambda x: (1 + beta.pdf(x[0], 2, 4)) / 4
        self.noise = lambda: 0.05 * np.random.normal(0, 1)
        self.treatment_function = lambda propensity, noise: 1 if np.random.random() <= propensity else 0
        self.outcome_function = lambda main, treat, treat_eff, noise: main + (treat - 0.5) * treat_eff + noise
        # E[Y1 - Y0 | X] = 0.5 * treat_eff(x) + 0.5*treat_eff(x) = treat_eff(x)
        self.cate = lambda x: self.treatment_effect(x)

    def add_custom_generated_data(self, main_effect: Callable[[List[float]], float],
                                  treatment_effect: Callable[[List[float]], float],
                                  treatment_propensity: Callable[[List[float]], float],
                                  noise: Callable[[], float],
                                  cate: Callable[[List[float]], float], dimensions: int,
                                  treatment_function: Callable[[float, float], float],
                                  outcome_function: Callable[[float, float, float, float], float],
                                  proxy_function: Callable[[List[float]], List[List[float]]] = None,
                                  distributions=None, sample_size: int = 500, name: str = None):
        if distributions is None:
            distributions = [np.random.random]
        if proxy_function is None:
            proxy_function = lambda features: [[feat] for feat in features]
        generator = data_generator.ProxyGenerator(main_effect=main_effect, treatment_effect=treatment_effect,
                                                  treatment_propensity=treatment_propensity, proxy_function=proxy_function,
                                                  noise=noise, cate=cate, treatment_function=treatment_function,
                                                  outcome_function=outcome_function, dimensions=dimensions,
                                                  distributions=distributions, name=name)
        return self.add_custom_generator(generator, sample_size=sample_size)

    def add_cevae_generated_data(self, distributions, proxy_function,
                                 treatment_function, outcome_function,
                                 dimensions, sample_size: int = 500, name: str=None):
        if distributions is None:
            distributions = [np.random.random]
        if proxy_function is None:
            proxy_function = lambda features: [[feat] for feat in features]
        generator = data_generator.CevaeGenerator(distributions, proxy_function,
                                                  treatment_function, outcome_function,
                                                  dimensions, name)
        return self.add_custom_generator(generator, sample_size=sample_size)

    # def add_custom_generated_proxy_data(self, main_effect: Callable[[List[float]], float],
    #                               treatment_effect: Callable[[List[float]], float],
    #                               treatment_propensity: Callable[[List[float]], float],
    #                               proxy_function: Callable[[List[float]], List[List[float]]],
    #                               noise: Callable[[], float],
    #                               cate: Callable[[List[float]], float], dimensions: int,
    #                               treatment_function: Callable[[float, float], float],
    #                               outcome_function: Callable[[float, float, float, float], float],
    #                               distributions=None, sample_size: int = 500, name: str=None):
    #     if distributions is None:
    #         distributions = [np.random.random]
    #     generator = data_generator.ProxyGenerator(main_effect=main_effect, treatment_effect=treatment_effect,
    #                                          treatment_propensity=treatment_propensity, proxy_function=proxy_function, noise=noise, cate=cate,
    #                                          treatment_function=treatment_function, outcome_function=outcome_function,
    #                                          dimensions=dimensions, distributions=distributions, name=name)
    #     return self.add_custom_generator(generator, sample_size=sample_size)

    def add_all_effects_generator(self, dimensions: int, sample_size: int = 500):
        main_effect = self.main_effect
        treatment_effect = self.treatment_effect
        treatment_propensity = self.treatment_propensity
        noise = self.noise
        treatment_function = self.treatment_function
        outcome_function = self.outcome_function
        cate = self.cate
        return self.add_custom_generated_data(main_effect, treatment_effect, treatment_propensity, noise, cate,
                                              dimensions, treatment_function, outcome_function,
                                              sample_size=sample_size, name='all_effects')

    def add_no_treatment_effect_generator(self, dimensions: int, sample_size: int = 500):
        main_effect = self.main_effect
        treatment_effect = lambda x: 0
        treatment_propensity = self.treatment_propensity
        noise = self.noise
        treatment_function = self.treatment_function
        outcome_function = self.outcome_function
        # E[Y1 - Y0 | X] = 0 as there is no dependence on treatment
        cate = lambda x: 0
        return self.add_custom_generated_data(main_effect, treatment_effect, treatment_propensity, noise, cate,
                                              dimensions, treatment_function, outcome_function,
                                              sample_size=sample_size, name='no_treatment_effect')

    def add_only_treatment_effect_generator(self, dimensions: int, sample_size: int = 500):
        main_effect = lambda x: 0
        treatment_effect = self.treatment_effect
        treatment_propensity = lambda x: 0.5
        noise = self.noise
        treatment_function = self.treatment_function
        outcome_function = self.outcome_function
        # E[Y1 - Y0|X] = E[0.5*treat_eff + 0.5*treat_eff] = treat_eff
        cate = lambda x: treatment_effect(x)
        return self.add_custom_generated_data(main_effect, treatment_effect, treatment_propensity, noise, cate,
                                              dimensions, treatment_function, outcome_function,
                                              sample_size=sample_size, name='only_treatment_effect')

    def add_biased_generator(self, dimensions: int, sample_size: int = 500):
        main_effect = lambda x: 0
        treatment_effect = lambda x: 1 if np.random.random() <= 0.05 else 0
        treatment_propensity = lambda x: 0.5
        noise = lambda : np.random.normal(0, 0.01)
        treatment_function = lambda propensity, noise: 1 if np.random.random() <= propensity else 0
        outcome_function = lambda main, treat, treat_eff, noise: 2 * treat * treat_eff + noise
        # E[Y1 - Y0 | X] = E[Y1|X] - E[Y0 | X] = 0.1 - 0 = 0.1
        cate = lambda x: 0.1
        return self.add_custom_generated_data(main_effect, treatment_effect, treatment_propensity, noise, cate,
                                              dimensions, treatment_function, outcome_function,
                                              sample_size=sample_size, name='biased_generator')

    def add_spiked_generator(self, dimensions: int, sample_size: int = 500):
        proxy_function = lambda features: [
            [features[0]],
            [features[1]],
            [features[2]],
            [features[3]],
            [features[4]]
        ]

        main_effect = self.main_effect
        # Spike around (0.5, 0.5) - equally spread through x and y
        # Very low std means a spike
        std = 0.01
        distr = multivariate_normal(cov=np.array([[std, 0], [0, std]]), mean=np.array([0.5, 0.5]),
                                    seed=42)
        treatment_effect = lambda x: distr.pdf([x[0], x[1]])
        # Closer to (0.5, 0.5), higher the chance of being treated
        treatment_propensity = lambda x: 1 - np.sqrt((x[0] - 0.5)**2 + (x[1] - 0.5)**2)
        noise = lambda: np.random.normal(0, 0.01)
        treatment_function = lambda propensity, noise: 1 if np.random.random() <= propensity else 0
        outcome_function = lambda main, treat, treat_eff, noise: dist.Bernoulli(logits=main + treat * treat_eff + noise).sample().cpu().item()
        # E[Y1 - Y0 | X] = E[Y1 | X] - E[Y0 | X] = 1 * treat_eff = treat_eff(x)
        cate = lambda x: treatment_effect(x)
        return self.add_custom_generated_data(main_effect, treatment_effect, treatment_propensity, noise, cate,
                                              dimensions, treatment_function, outcome_function, proxy_function,
                                              sample_size=sample_size, name='spiked_generator')

    def add_synthetic_generator(self, dimensions: int, sample_size: int = 500):
        # Normal - Age
        # Inverse exponential - Income
        # Uniform - Day of the week
        distributions = [np.random.uniform]
        proxy_function = lambda z: [
            [z[0]],
            [z[1]],
            [z[2]],
            [z[3]],
            [z[4]],
        ]
        noise = 0
        treatment_function = lambda z: dist.Bernoulli(
            np.clip(np.sin(np.pi * z[0] * z[1]), 0.1, 0.9)
        ).sample().cpu().item()
        outcome_function = lambda z, t: np.sin(np.pi * z[0] * z[1]) \
                                        + 2 * (z[2] - 0.5) ** 2 \
                                        + z[3] \
                                        + 0.5 * z[4] \
                                        + (t - 0.5) * (z[0] + z[1]) / 2 \
                                        + noise
        return self.add_cevae_generated_data(distributions, proxy_function, treatment_function, outcome_function,
                                             dimensions, sample_size=sample_size, name='easy_1_generator')

    def add_easy_generator(self, dimensions, sample_size, proxy_noise_weight):
        # Normal - Age
        # Inverse exponential - Income
        # Uniform - Day of the week
        # distributions = [lambda: (dist.Bernoulli(0.5).sample().cpu().item() * 2) - 1]
        distributions = [lambda: dist.Uniform(-2, 2).sample().cpu().item()]
        proxy_function = lambda z: [
            [dist.Normal(z[0], proxy_noise_weight).sample().cpu().item()],
        ]
        # noise = np.random.uniform()
        treatment_function = lambda z: dist.Bernoulli(logits=z[0]).sample().cpu().item()

        def outcome_function(z, t, mean=False):
            if mean:
                return dist.Normal(t * z[0] + z[0], 0.5).mean.cpu().item()
            else:
                return dist.Normal(t * z[0] + z[0], 0.5).sample().cpu().item()

        return self.add_cevae_generated_data(distributions, proxy_function, treatment_function, outcome_function,
                                             dimensions, sample_size=sample_size, name='easy_1_generator')

    def add_constant_treatment_effect_generator(self, dimensions: int, sample_size: int = 500):
        proxy_function = lambda features: [
            [features[0]],
            [features[1]],
            [features[2]],
            [features[3]],
            [features[4]]
        ]

        main_effect = self.main_effect
        # Spike around (0.5, 0.5) - equally spread through x and y
        # Very low std means a spike
        std = 0.01
        distr = multivariate_normal(cov=np.array([[std, 0], [0, std]]), mean=np.array([0.5, 0.5]),
                                    seed=42)
        treatment_effect = lambda x: 0.3
        # Closer to (0.5, 0.5), higher the chance of being treated
        treatment_propensity = lambda x: 1 - np.sqrt((x[0] - 0.5) ** 2 + (x[1] - 0.5) ** 2)
        noise = lambda: np.random.normal(0, 0.01)
        treatment_function = lambda propensity, noise: 1 if np.random.random() <= propensity else 0
        outcome_function = lambda main, treat, treat_eff, noise: main + treat * treat_eff + noise
        # E[Y1 - Y0 | X] = E[Y1 | X] - E[Y0 | X] = 1 * treat_eff = treat_eff(x)
        cate = lambda x: treatment_effect(x)
        return self.add_custom_generated_data(main_effect, treatment_effect, treatment_propensity, noise, cate,
                                              dimensions, treatment_function, outcome_function, proxy_function,
                                              sample_size=sample_size, name='spiked_generator')

    def add_constant_proxied_treatment_effect_generator(self, dimensions: int, sample_size: int = 500):
        proxy_function = lambda features: [
            # [np.random.normal(features[0], 0.5),
            #  np.random.normal(features[0], 0.5),
            #  np.random.normal(features[0], 0.5)],
            # [np.random.normal(features[1], 0.2),
            #  np.random.normal(features[1], 0.2),
            #  np.random.normal(features[1], 0.2)],
            [features[0],
             features[0],
             features[0]],
            [features[1],
             features[1],
             features[1]],
            [features[2]],
            [features[3]],
            [features[4]]
        ]

        main_effect = self.main_effect
        # Spike around (0.5, 0.5) - equally spread through x and y
        # Very low std means a spike
        std = 0.01
        distr = multivariate_normal(cov=np.array([[std, 0], [0, std]]), mean=np.array([0.2, 0.6]),
                                    seed=42)
        treatment_effect = lambda x: distr.pdf([x[0], x[1]]) / 10
        # Closer to (0.5, 0.5), higher the chance of being treated
        treatment_propensity = lambda x: 1 - np.sqrt((x[0] - 0.5) ** 2 + (x[1] - 0.5) ** 2)
        noise = lambda: np.random.normal(0, 0.01)
        treatment_function = lambda propensity, noise: 1 if np.random.random() <= propensity else 0
        outcome_function = lambda main, treat, treat_eff, noise: main + treat * treat_eff + noise
        # E[Y1 - Y0 | X] = E[Y1 | X] - E[Y0 | X] = 1 * treat_eff = treat_eff(x)
        cate = lambda x: treatment_effect(x)
        return self.add_custom_generated_data(main_effect, treatment_effect, treatment_propensity, noise, cate,
                                              dimensions, treatment_function, outcome_function, proxy_function,
                                              sample_size=sample_size, name='spiked_generator')

    def add_toy_dataset_generator(self, dimensions: int, sample_size: int = 500):
        # z = dist.Bernoulli(0.5).sample([sample_size])
        # x = dist.Normal(z, 5 * z + 3 * (1 - z)).sample([dimensions]).t()
        # t = dist.Bernoulli(0.75 * z + 0.25 * (1 - z)).sample()
        # y = dist.Bernoulli(logits=3 * (z + 2 * (2 * t - 2))).sample()

        # Compute true ite for evaluation (via Monte Carlo approximation).
        # t0_t1 = torch.tensor([[0.0], [1.0]])
        # y_t0, y_t1 = dist.Bernoulli(logits=3 * (z + 2 * (2 * t0_t1 - 2))).mean
        # true_ite = y_t1 - y_t0
        # return x, t, y, true_ite

        # Normal - Age
        # Inverse exponential - Income
        # Uniform - Day of the week
        distributions = [lambda: dist.Bernoulli(0.5).sample().cpu().item()]
        # distributions = [lambda: dist.Normal(0.4, 0.1).sample().cpu().item()]
        # distributions = [lambda: dist.Uniform(0.0, 1.0).sample().cpu().item()]
        proxy_function = lambda z: [
            # [dist.Normal(z[0], 5 * z[0] + 3 * (1 - z[0])).sample().cpu().item(),
            #  dist.Normal(z[0], 5 * z[0] + 3 * (1 - z[0])).sample().cpu().item(),
            #  dist.Normal(z[0], 5 * z[0] + 3 * (1 - z[0])).sample().cpu().item()]
            [dist.Normal(z[0], 5 * z[0] + 3 * (1 - z[0])).sample().cpu().item()]
        ]
        treatment_function = lambda z: z[0]
        # treatment_function = lambda z: dist.Bernoulli(0.75 * z[0] + 0.25 * (1 - z[0])).sample().cpu().item()
        outcome_function = lambda z, t: dist.Bernoulli(logits=3 * (z[0] + 2 * (2 * t - 1))).sample().cpu().item()
        return self.add_cevae_generated_data(distributions, proxy_function, treatment_function, outcome_function,
                                              dimensions, sample_size=sample_size, name='cevae_toy_generator')

    def add_spiked_proxy_generator(self, dimensions: int, sample_size: int = 500):
        main_effect = self.main_effect
        # Spike around (0.5, 0.5) - equally spread through x and y
        # Very low std means a spike
        std = 0.01
        distr = multivariate_normal(cov=np.array([[std, 0], [0, std]]), mean=np.array([0.5, 0.5]),
                                    seed=42)
        proxy_function = lambda features: [
            # [np.random.normal(features[0], 0.1),
            #  np.random.normal(features[0], 0.1),
            #  np.random.normal(features[0], 0.1)],
            [features[0], features[0], features[0]],
            [features[1]],
            [features[2]],
            [features[3]],
            [features[4]]
        ]
        # treatment_effect = lambda x: distr.pdf([x[0], x[1]])
        # Closer to (0.5, 0.5), higher the chance of being treated
        # treatment_propensity = lambda x: 1 - np.sqrt((x[0] - 0.5)**2 + (x[1] - 0.5)**2)
        # noise = lambda: np.random.normal(0, 0.01)
        # treatment_function = lambda propensity, noise: 1 if np.random.random() <= propensity else 0
        # outcome_function = lambda main, treat, treat_eff, noise: main + treat * treat_eff + noise
        # E[Y1 - Y0 | X] = E[Y1 | X] - E[Y0 | X] = 1 * treat_eff = treat_eff(x)
        # cate = lambda x: treatment_effect(x)
        std = 0.01
        # distr = multivariate_normal(cov=np.array([[std, 0], [0, std]]), mean=np.array([0.5, 0.5]),
        #                             seed=42)
        treatment_effect = lambda x: x[0] ** 2
        # Closer to (0.5, 0.5), higher the chance of being treated
        treatment_propensity = lambda x: 1 - np.sqrt((x[0] - 0.5) ** 2 + (x[1] - 0.5) ** 2)
        noise = lambda: np.random.normal(0, 0.01)
        treatment_function = lambda propensity, noise: 1 if np.random.random() <= propensity else 0
        outcome_function = lambda main, treat, treat_eff, noise: main + treat * treat_eff + noise
        # E[Y1 - Y0 | X] = E[Y1 | X] - E[Y0 | X] = 1 * treat_eff = treat_eff(x)
        cate = lambda x: treatment_effect(x)
        return self.add_custom_generated_proxy_data(main_effect, treatment_effect, treatment_propensity, proxy_function, noise, cate,
                                              dimensions, treatment_function, outcome_function,
                                              sample_size=sample_size, name='spiked_proxy_generator')


    def add_noisy_spiked_proxy_generator(self, dimensions: int, sample_size: int = 500):
        main_effect = self.main_effect
        # Spike around (0.5, 0.5) - equally spread through x and y
        # Very low std means a spike
        std = 0.01
        distr = multivariate_normal(cov=np.array([[std, 0], [0, std]]), mean=np.array([0.5, 0.5]),
                                    seed=42)
        proxy_function = lambda features: [
            [np.random.normal(features[0], 0.3),
             np.random.normal(features[0], 0.3),
             np.random.normal(features[0], 0.3)],
            # [features[0],
            #  features[0],
            #  features[0]],
            #  [features[0]],
            [np.random.normal(features[1], 0.25),
             np.random.normal(features[1], 0.25),
             np.random.normal(features[1], 0.25)],
            # [features[1]],
            [features[2]],
            [features[3]],
            [features[4]]
        ]
        # treatment_effect = lambda x: distr.pdf([x[0], x[1]])
        # Closer to (0.5, 0.5), higher the chance of being treated
        # treatment_propensity = lambda x: 1 - np.sqrt((x[0] - 0.5)**2 + (x[1] - 0.5)**2)
        # noise = lambda: np.random.normal(0, 0.01)
        # treatment_function = lambda propensity, noise: 1 if np.random.random() <= propensity else 0
        # outcome_function = lambda main, treat, treat_eff, noise: main + treat * treat_eff + noise
        # E[Y1 - Y0 | X] = E[Y1 | X] - E[Y0 | X] = 1 * treat_eff = treat_eff(x)
        # cate = lambda x: treatment_effect(x)
        std = 0.01
        # distr = multivariate_normal(cov=np.array([[std, 0], [0, std]]), mean=np.array([0.5, 0.5]),
        #                             seed=42)
        treatment_effect = lambda x: x[0] ** 2 + x[1] ** 2
        # Closer to (0.5, 0.5), higher the chance of being treated
        treatment_propensity = lambda x: 1 - np.sqrt((x[0] - 0.5) ** 2 + (x[1] - 0.5) ** 2)
        noise = lambda: np.random.normal(0, 0.01)
        treatment_function = lambda propensity, noise: 1 if np.random.random() <= propensity else 0
        import pyro.distributions as dist
        # outcome_function = lambda main, treat, treat_eff, noise: dist.Bernoulli(logits=main + treat * treat_eff + noise).sample().item()
        outcome_function = lambda main, treat, treat_eff, noise: main + treat * treat_eff + noise
        # E[Y1 - Y0 | X] = E[Y1 | X] - E[Y0 | X] = 1 * treat_eff = treat_eff(x)
        cate = lambda x: 1
        return self.add_custom_generated_data(main_effect, treatment_effect, treatment_propensity, noise, cate,
                                              dimensions, treatment_function, outcome_function, proxy_function,
                                              sample_size=sample_size, name='spiked_proxy_generator')
